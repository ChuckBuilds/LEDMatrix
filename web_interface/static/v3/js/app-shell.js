/* global debugLog */
// SSE wiring + full Alpine app() implementation and tab logic
// Extracted from templates/v3/base.html so browsers cache it as a static asset.
        // Assign to window so reconnectSSE() in app.js can reach them.
        window.statsSource = new EventSource('/api/v3/stream/stats');
        window.displaySource = new EventSource('/api/v3/stream/display');

        window.statsSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            updateSystemStats(data);
        };

        window.displaySource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            updateDisplayPreview(data);
        };

        function _setConnectionStatus(connected, reconnecting) {
            const el = document.getElementById('connection-status');
            if (!el) return;
            if (connected) {
                el.innerHTML = `
                    <div class="w-2 h-2 bg-green-500 rounded-full"></div>
                    <span class="text-gray-600">Connected</span>
                `;
            } else if (reconnecting) {
                el.innerHTML = `
                    <div class="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></div>
                    <span class="text-gray-600">Reconnecting…</span>
                `;
            } else {
                el.innerHTML = `
                    <div class="w-2 h-2 bg-red-500 rounded-full"></div>
                    <span class="text-gray-600" title="Connection lost — try refreshing the page">Disconnected</span>
                `;
            }
        }

        var _statsErrorCount = 0;

        // Named on window so reconnectSSE() in app.js can reattach them after
        // replacing the EventSource instances.
        window._statsOpenHandler = function() {
            _statsErrorCount = 0;
            _setConnectionStatus(true, false);
        };
        window._statsErrorHandler = function() {
            _statsErrorCount++;
            // EventSource readyState 0 = CONNECTING (auto-retrying), 2 = CLOSED
            var reconnecting = window.statsSource.readyState === EventSource.CONNECTING;
            _setConnectionStatus(false, reconnecting && _statsErrorCount <= 3);
        };
        window._displayErrorHandler = function() {
            // Display stream errors don't change the status badge but log to console
            // so failures aren't completely silent.
            console.warn('LEDMatrix: display preview stream error (readyState=' + window.displaySource.readyState + ')');
        };

        window.statsSource.addEventListener('open', window._statsOpenHandler);
        window.statsSource.addEventListener('error', window._statsErrorHandler);
        window.displaySource.addEventListener('error', window._displayErrorHandler);

        // Reset any time the currently-active warning clears, so a future
        // (new) occurrence shows the banner again even if this one was dismissed.
        window._powerWarningDismissed = false;

        window.dismissPowerWarningBanner = function() {
            const banner = document.getElementById('power-warning-banner');
            if (banner) banner.style.display = 'none';
            window._powerWarningDismissed = true;
        };

        // Labels for whichever flags from _get_power_status() are set (pass
        // suffix='_occurred' for the "happened earlier" variant), used to
        // build accurate banner/tooltip text instead of hardcoding
        // "under-voltage" for what may actually be throttling/freq-capping/
        // thermal limiting.
        function _activePowerConditionLabels(power, suffix) {
            suffix = suffix || '_now';
            const labels = [];
            if (power['under_voltage' + suffix]) labels.push('under-voltage');
            if (power['throttled' + suffix]) labels.push('throttling');
            if (power['freq_capped' + suffix]) labels.push('CPU frequency capped');
            if (power['soft_temp_limit' + suffix]) labels.push('soft thermal limit');
            return labels;
        }

        function updatePowerStatus(power) {
            const statEl = document.getElementById('power-stat');
            const banner = document.getElementById('power-warning-banner');
            const bannerText = document.getElementById('power-warning-banner-text');

            if (!power) {
                if (statEl) statEl.classList.add('hidden');
                if (banner) {
                    banner.style.display = 'none';
                    // Let a future occurrence show the banner again rather
                    // than leaving stale text/visibility from before this
                    // (likely transient) missing-data tick.
                    window._powerWarningDismissed = false;
                }
                return;
            }

            const activeNow = power.under_voltage_now || power.throttled_now ||
                power.freq_capped_now || power.soft_temp_limit_now;
            const occurredEarlier = power.under_voltage_occurred || power.throttled_occurred ||
                power.freq_capped_occurred || power.soft_temp_limit_occurred;

            if (statEl) {
                statEl.classList.remove('text-red-600', 'text-yellow-600');
                if (activeNow) {
                    statEl.classList.remove('hidden');
                    statEl.classList.add('flex', 'text-red-600');
                    statEl.title = _activePowerConditionLabels(power).join('/') +
                        ' detected right now — check your power supply and cooling';
                } else if (occurredEarlier) {
                    statEl.classList.remove('hidden');
                    statEl.classList.add('flex', 'text-yellow-600');
                    const occurredLabels = _activePowerConditionLabels(power, '_occurred');
                    statEl.title = (occurredLabels.length ? occurredLabels.join('/') : 'An issue') +
                        ' was detected earlier (currently OK)';
                } else {
                    statEl.classList.add('hidden');
                }
            }

            if (banner) {
                if (activeNow) {
                    if (bannerText) {
                        const labels = _activePowerConditionLabels(power);
                        bannerText.textContent = (labels.length ? labels.join('/') : 'A power/thermal issue') +
                            ' detected right now — the display may flicker or degrade. Check your power supply and cooling.';
                    }
                    if (!window._powerWarningDismissed) {
                        banner.style.display = '';
                    }
                } else {
                    banner.style.display = 'none';
                    // Let a future occurrence show the banner again.
                    window._powerWarningDismissed = false;
                }
            }
        }

        function updateSystemStats(data) {
            // Update CPU in header
            const cpuEl = document.getElementById('cpu-stat');
            if (cpuEl && data.cpu_percent !== undefined) {
                const spans = cpuEl.querySelectorAll('span');
                if (spans.length > 0) spans[spans.length - 1].textContent = data.cpu_percent + '%';
            }

            // Update Memory in header
            const memEl = document.getElementById('memory-stat');
            if (memEl && data.memory_used_percent !== undefined) {
                const spans = memEl.querySelectorAll('span');
                if (spans.length > 0) spans[spans.length - 1].textContent = data.memory_used_percent + '%';
            }

            // Update Temperature in header
            const tempEl = document.getElementById('temp-stat');
            if (tempEl && data.cpu_temp !== undefined) {
                const spans = tempEl.querySelectorAll('span');
                if (spans.length > 0) spans[spans.length - 1].textContent = data.cpu_temp + '°C';
            }

            // Update Power (under-voltage / throttling) status in header + banner
            updatePowerStatus(data.power);

            // Update Overview tab stats (if visible)
            const cpuUsageEl = document.getElementById('cpu-usage');
            if (cpuUsageEl && data.cpu_percent !== undefined) {
                cpuUsageEl.textContent = data.cpu_percent + '%';
            }

            const memUsageEl = document.getElementById('memory-usage');
            if (memUsageEl && data.memory_used_percent !== undefined) {
                memUsageEl.textContent = data.memory_used_percent + '%';
            }

            const cpuTempEl = document.getElementById('cpu-temp');
            if (cpuTempEl && data.cpu_temp !== undefined) {
                cpuTempEl.textContent = data.cpu_temp + '°C';
            }

            const displayStatusEl = document.getElementById('display-status');
            if (displayStatusEl) {
                displayStatusEl.textContent = data.service_active ? 'Active' : 'Inactive';
                displayStatusEl.className = data.service_active ?
                    'text-lg font-medium text-green-600' :
                    'text-lg font-medium text-red-600';
            }
        }

        window.__onDemandStore = window.__onDemandStore || {
            loading: true,
            state: {},
            service: {},
            error: null,
            lastUpdated: null
        };

        document.addEventListener('alpine:init', () => {
            // On-Demand state store
            if (window.Alpine && !window.Alpine.store('onDemand')) {
                window.Alpine.store('onDemand', {
                    loading: window.__onDemandStore.loading,
                    state: window.__onDemandStore.state,
                    service: window.__onDemandStore.service,
                    error: window.__onDemandStore.error,
                    lastUpdated: window.__onDemandStore.lastUpdated
                });
            }
            if (window.Alpine) {
                window.__onDemandStore = window.Alpine.store('onDemand');
            }
            
            // Plugin state store - centralized state management for plugins
            // Used primarily by HTMX-loaded plugin config partials
            if (window.Alpine && !window.Alpine.store('plugins')) {
                window.Alpine.store('plugins', {
                    // Track which plugin configs have been loaded
                    loadedConfigs: {},
                    
                    // Mark a plugin config as loaded
                    markLoaded(pluginId) {
                        this.loadedConfigs[pluginId] = true;
                    },
                    
                    // Check if a plugin config is loaded
                    isLoaded(pluginId) {
                        return !!this.loadedConfigs[pluginId];
                    },
                    
                    // Refresh a plugin config tab via HTMX
                    refreshConfig(pluginId) {
                        const container = document.querySelector(`#plugin-config-${pluginId}`);
                        if (container && window.htmx) {
                            htmx.ajax('GET', `/v3/partials/plugin-config/${pluginId}`, {
                                target: container,
                                swap: 'innerHTML'
                            });
                        }
                    }
                });
            }
        });


        // Alpine.js app function - full implementation
        function app() {
            // If Alpine is already initialized, get the current component and enhance it
            let baseComponent = {};
            if (window.Alpine) {
                const appElement = document.querySelector('[x-data]');
                if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                    baseComponent = appElement._x_dataStack[0];
                }
            }
            
            const fullImplementation = {
                activeTab: (function() {
                    // Auto-open WiFi tab when in AP mode (192.168.4.x)
                    const isAPMode = window.location.hostname === '192.168.4.1' || 
                                   window.location.hostname.startsWith('192.168.4.');
                    return isAPMode ? 'wifi' : 'overview';
                })(),
                mobileNavOpen: false,
                installedPlugins: [],

                init() {
                    // Prevent multiple initializations
                    if (this._initialized) {
                        return;
                    }
                    this._initialized = true;
                    
                    // Load plugins on page load so tabs are available on any page, regardless of active tab
                    // First check if plugins are already in window.installedPlugins (from plugins_manager.js)
                    if (typeof window.installedPlugins !== 'undefined' && Array.isArray(window.installedPlugins) && window.installedPlugins.length > 0) {
                        this.installedPlugins = window.installedPlugins;
                        debugLog('Initialized installedPlugins from global:', this.installedPlugins.length);
                        // Ensure tabs are updated immediately
                        this.$nextTick(() => {
                            this.updatePluginTabs();
                        });
                    } else if (!this.installedPlugins || this.installedPlugins.length === 0) {
                        // Load plugins asynchronously, but ensure tabs update when done
                        this.loadInstalledPlugins().then(() => {
                            // Ensure tabs are updated after loading
                            this.$nextTick(() => {
                                this.updatePluginTabs();
                            });
                        }).catch(err => {
                            console.error('Error loading plugins in init:', err);
                            // Still try to update tabs in case some plugins are available
                            this.$nextTick(() => {
                                this.updatePluginTabs();
                            });
                        });
                    } else {
                        // Plugins already loaded, just update tabs
                        this.$nextTick(() => {
                            this.updatePluginTabs();
                        });
                    }
                    
                    // Ensure content loads for the active tab
                    this.$watch('activeTab', (newTab, oldTab) => {
                        // Update plugin tab states when activeTab changes
                        if (typeof this.updatePluginTabStates === 'function') {
                            this.updatePluginTabStates();
                        }
                        // Screen readers announce the current tab (covers every
                        // path that changes tabs: clicks, search deep links,
                        // the getting-started checklist)
                        if (typeof window.updateNavAriaCurrent === 'function') {
                            window.updateNavAriaCurrent(newTab);
                        }
                        // Floating preview hides on Overview (full preview
                        // there), reappears per its saved state elsewhere
                        if (typeof window.updateFloatingPreviewVisibility === 'function') {
                            window.updateFloatingPreviewVisibility(newTab);
                        }
                        // Trigger content load when tab changes
                        this.$nextTick(() => {
                            this.loadTabContent(newTab);
                        });
                    });

                    // Load initial tab content
                    this.$nextTick(() => {
                        this.loadTabContent(this.activeTab);
                        if (typeof window.updateNavAriaCurrent === 'function') {
                            window.updateNavAriaCurrent(this.activeTab);
                        }
                    });

                    // Listen for plugin updates from pluginManager
                    document.addEventListener('pluginsUpdated', (event) => {
                        debugLog('Received pluginsUpdated event:', event.detail.plugins.length, 'plugins');
                        this.installedPlugins = event.detail.plugins;
                        this.updatePluginTabs();
                    });

                    // Also listen for direct window.installedPlugins changes
                    // Store the actual value in a private property to avoid infinite loops
                    let _installedPluginsValue = this.installedPlugins || [];
                    
                    // Only define the property if it doesn't already exist or if it's configurable
                    const existingDescriptor = Object.getOwnPropertyDescriptor(window, 'installedPlugins');
                    if (!existingDescriptor || existingDescriptor.configurable) {
                        // Delete existing property if it exists and is configurable
                        if (existingDescriptor) {
                            delete window.installedPlugins;
                        }
                        
                        Object.defineProperty(window, 'installedPlugins', {
                            set: (value) => {
                                const newPlugins = value || [];
                                const oldIds = (_installedPluginsValue || []).map(p => p.id).sort().join(',');
                                const newIds = newPlugins.map(p => p.id).sort().join(',');

                                // Always take the new list — same-ID updates
                                // still carry changed metadata/enabled state.
                                _installedPluginsValue = newPlugins;
                                this.installedPlugins = newPlugins;
                                // Only rebuild the tab row when the ID set
                                // actually changed.
                                if (oldIds !== newIds) {
                                    debugLog('window.installedPlugins changed:', newPlugins.length, 'plugins');
                                    this.updatePluginTabs();
                                }
                            },
                            get: () => _installedPluginsValue,
                            configurable: true  // Allow redefinition if needed
                        });
                    } else {
                        // Property already exists and is not configurable, just update the value
                        if (typeof window.installedPlugins !== 'undefined') {
                            _installedPluginsValue = window.installedPlugins;
                        }
                    }

                },

                loadTabContent(tab) {
                    const contentEl = document.getElementById(tab + '-content');
                    // data-loaded: already fetched. data-loading: a fetch is queued or in
                    // flight. Both guard against re-entry so a panel loads exactly once, even
                    // if the tab is reopened before an in-progress (or polling) load settles.
                    if (!contentEl || contentEl.hasAttribute('data-loaded') || contentEl.hasAttribute('data-loading')) return;
                    const url = contentEl.getAttribute('hx-get');
                    if (!url) return;

                    contentEl.setAttribute('data-loading', 'true');

                    // htmx.ajax issues the request and swaps the response into the panel
                    // directly, so it works even before htmx has wired up the element's
                    // hx-trigger listeners. data-loaded is stamped on success so the panel
                    // loads once; the activeTab check drops loads for a tab the user navigated
                    // away from while htmx was still loading (avoids fetching hidden panels).
                    const swap = contentEl.getAttribute('hx-swap') || 'innerHTML';
                    const load = () => {
                        if (this.activeTab !== tab || contentEl.hasAttribute('data-loaded')) {
                            contentEl.removeAttribute('data-loading');
                            return;
                        }
                        return htmx.ajax('GET', url, { target: contentEl, swap: swap })
                            .then(() => contentEl.setAttribute('data-loaded', 'true'))
                            .catch(() => {}) // leave unstamped on failure so it can retry
                            .finally(() => contentEl.removeAttribute('data-loading'));
                    };

                    if (typeof htmx !== 'undefined') {
                        load();
                        return;
                    }

                    // htmx is loaded from a CDN and may not be ready yet. Poll until it is,
                    // then load; if it never arrives, fall back to a direct fetch.
                    let tries = 0;
                    const timer = setInterval(() => {
                        if (typeof htmx !== 'undefined') {
                            clearInterval(timer);
                            load();
                        } else if (++tries > 100) { // ~10s
                            clearInterval(timer);
                            contentEl.removeAttribute('data-loading');
                            if (tab === 'overview' && typeof loadOverviewDirect === 'function') loadOverviewDirect();
                            else if (tab === 'wifi' && typeof loadWifiDirect === 'function') loadWifiDirect();
                            else if (tab === 'plugins' && typeof loadPluginsDirect === 'function') loadPluginsDirect();
                            else if (tab === 'tools') {
                                fetch('/v3/partials/tools')
                                    .then(r => {
                                        if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
                                        return r.text();
                                    })
                                    .then(html => {
                                        contentEl.innerHTML = html;
                                        contentEl.setAttribute('data-loaded', 'true');
                                        if (window.Alpine) window.Alpine.initTree(contentEl);
                                    })
                                    .catch(err => {
                                        console.error('Failed to load tools content:', err);
                                        contentEl.innerHTML = '<div class="bg-red-50 border border-red-200 rounded-lg p-4"><p class="text-red-800">Failed to load Tools. Please refresh the page.</p></div>';
                                    });
                            }
                        }
                    }, 100);
                },

                async loadInstalledPlugins() {
                    // If pluginManager exists (plugins.html is loaded), delegate to it
                    if (window.pluginManager) {
                        debugLog('[FULL] Delegating plugin loading to pluginManager...');
                        await window.pluginManager.loadInstalledPlugins();
                        // pluginManager should set window.installedPlugins, so update our component
                        if (window.installedPlugins && Array.isArray(window.installedPlugins)) {
                            this.installedPlugins = window.installedPlugins;
                            debugLog('[FULL] Updated component plugins from window.installedPlugins:', this.installedPlugins.length);
                        }
                        this.updatePluginTabs();
                        return;
                    }

                    // Otherwise, load plugins directly (fallback for when plugins.html isn't loaded)
                    try {
                        debugLog('[FULL] Loading installed plugins directly...');
                        const data = await getInstalledPluginsSafe();

                        if (data.status === 'success') {
                            this.installedPlugins = data.data.plugins || [];
                            // Also update window.installedPlugins for consistency
                            window.installedPlugins = this.installedPlugins;
                            debugLog(`[FULL] Loaded ${this.installedPlugins.length} plugins:`, this.installedPlugins.map(p => p.id));
                            
                            // Debug: Log enabled status for each plugin
                            this.installedPlugins.forEach(plugin => {
                                debugLog(`[DEBUG Alpine] Plugin ${plugin.id}: enabled=${plugin.enabled} (type: ${typeof plugin.enabled})`);
                            });
                            
                            this.updatePluginTabs();
                        } else {
                            console.error('[FULL] Failed to load plugins:', data.message);
                        }
                    } catch (error) {
                        console.error('[FULL] Error loading installed plugins:', error);
                    }
                },

                updatePluginTabs(retryCount = 0) {
                    debugLog('[FULL] updatePluginTabs called (retryCount:', retryCount, ')');
                    const maxRetries = 5;

                    // Debounce: Clear any pending update
                    if (this._updatePluginTabsTimeout) {
                        clearTimeout(this._updatePluginTabsTimeout);
                    }
                    
                    // For first call or retries, execute immediately to ensure tabs appear quickly
                    if (retryCount === 0) {
                        // First call - execute immediately, then debounce subsequent calls
                        this._doUpdatePluginTabs(retryCount);
                    } else {
                        // Retry - execute immediately
                        this._doUpdatePluginTabs(retryCount);
                    }
                },
                
                _doUpdatePluginTabs(retryCount = 0) {
                    const maxRetries = 5;

                    // Use component's installedPlugins first (most up-to-date), then global, then empty array
                    const pluginsToShow = (this.installedPlugins && this.installedPlugins.length > 0) 
                        ? this.installedPlugins 
                        : (window.installedPlugins || []);
                    
                    debugLog('[FULL] _doUpdatePluginTabs called with:', pluginsToShow.length, 'plugins (attempt', retryCount + 1, ')');
                    debugLog('[FULL] Plugin sources:', {
                        componentPlugins: this.installedPlugins?.length || 0,
                        windowPlugins: window.installedPlugins?.length || 0,
                        using: pluginsToShow.length > 0 ? (this.installedPlugins?.length > 0 ? 'component' : 'window') : 'none'
                    });
                    
                    // Check if plugin list actually changed by comparing IDs
                    const currentPluginIds = pluginsToShow.map(p => p.id).sort().join(',');
                    const lastRenderedIds = (this._lastRenderedPluginIds || '');
                    
                    // Only skip if we have plugins and they match (don't skip if both are empty)
                    if (currentPluginIds === lastRenderedIds && retryCount === 0 && currentPluginIds.length > 0) {
                        // Plugin list hasn't changed, skip update
                        debugLog('[FULL] Plugin list unchanged, skipping update');
                        return;
                    }
                    
                    // If we have no plugins and haven't rendered anything yet, still try to render (might be first load)
                    if (pluginsToShow.length === 0 && retryCount === 0) {
                        debugLog('[FULL] No plugins to show, but will retry in case they load...');
                        if (retryCount < maxRetries) {
                            setTimeout(() => {
                                this._doUpdatePluginTabs(retryCount + 1);
                            }, 500);
                        }
                        return;
                    }
                    
                    // Store the current plugin IDs for next comparison
                    this._lastRenderedPluginIds = currentPluginIds;

                    const pluginTabsRow = document.getElementById('plugin-tabs-row');
                    const pluginTabsNav = pluginTabsRow?.querySelector('nav');

                    debugLog('[FULL] Plugin tabs elements:', {
                        pluginTabsRow: !!pluginTabsRow,
                        pluginTabsNav: !!pluginTabsNav,
                        bodyExists: !!document.body,
                        installedPlugins: pluginsToShow.length,
                        pluginIds: pluginsToShow.map(p => p.id)
                    });

                    if (!pluginTabsRow || !pluginTabsNav) {
                        if (retryCount < maxRetries) {
                            console.warn('[FULL] Plugin tabs container not found, retrying in 500ms... (attempt', retryCount + 1, 'of', maxRetries, ')');
                            setTimeout(() => {
                                this._doUpdatePluginTabs(retryCount + 1);
                            }, 500);
                        } else {
                            console.error('[FULL] Plugin tabs container not found after maximum retries. Elements:', {
                                pluginTabsRow: document.getElementById('plugin-tabs-row'),
                                pluginTabsNav: document.getElementById('plugin-tabs-row')?.querySelector('nav'),
                                allNavs: document.querySelectorAll('nav').length
                            });
                        }
                        return;
                    }

                    debugLog(`[FULL] Updating plugin tabs for ${pluginsToShow.length} plugins`);

                    // Always show the plugin tabs row (Plugin Manager should always be available)
                    debugLog('[FULL] Ensuring plugin tabs row is visible');
                    pluginTabsRow.style.display = 'block';

                    // Clear existing plugin tabs (except the Plugin Manager tab)
                    const existingTabs = pluginTabsNav.querySelectorAll('.plugin-tab');
                    debugLog(`[FULL] Removing ${existingTabs.length} existing plugin tabs`);
                    existingTabs.forEach(tab => tab.remove());

                    // Add tabs for each installed plugin
                    debugLog('[FULL] Adding tabs for plugins:', pluginsToShow.map(p => p.id));
                    pluginsToShow.forEach(plugin => {
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
                        // Build the <i class="..."> + label as DOM nodes so a
                        // hostile plugin.icon (e.g. containing a quote) can't
                        // break out of the attribute. escapeHtml only escapes
                        // <, >, &, not ", so attribute-context interpolation
                        // would be unsafe.
                        const iconEl = document.createElement('i');
                        iconEl.className = plugin.icon || 'fas fa-puzzle-piece';
                        const labelNode = document.createTextNode(plugin.name || plugin.id);
                        tabButton.replaceChildren(iconEl, labelNode);

                        // Insert before the closing </nav> tag
                        pluginTabsNav.appendChild(tabButton);
                        debugLog('[FULL] Added tab for plugin:', plugin.id);
                    });

                    debugLog('[FULL] Plugin tabs update completed. Total tabs:', pluginTabsNav.querySelectorAll('.plugin-tab').length);
                },

                updatePluginTabStates() {
                    // Update active state of all plugin tabs when activeTab changes
                    const pluginTabsNav = document.getElementById('plugin-tabs-row')?.querySelector('nav');
                    if (!pluginTabsNav) return;
                    
                    const pluginTabs = pluginTabsNav.querySelectorAll('.plugin-tab');
                    pluginTabs.forEach(tab => {
                        const pluginId = tab.getAttribute('data-plugin-id');
                        if (pluginId && this.activeTab === pluginId) {
                            tab.classList.add('nav-tab-active');
                        } else {
                            tab.classList.remove('nav-tab-active');
                        }
                    });
                },

                showNotification(message, type = 'info') {
                    // Use global notification widget
                    if (typeof window.showNotification === 'function') {
                        window.showNotification(message, type);
                    } else {
                        debugLog(`[${type.toUpperCase()}]`, message);
                    }
                },

                escapeHtml(text) {
                    const div = document.createElement('div');
                    div.textContent = text;
                    return div.innerHTML;
                },

                async refreshPlugins() {
                    await this.loadInstalledPlugins();
                    await this.searchPluginStore();
                    this.showNotification('Plugin list refreshed', 'success');
                },



                async loadPluginConfig(pluginId) {
                    debugLog('Loading config for plugin:', pluginId);
                    this.loading = true;

                    try {
                        // Load config, schema, and installed plugins (for web_ui_actions) in parallel
                        // Use batched API if available for better performance
                        let configData, schemaData, pluginsData;
                        
                        if (window.PluginAPI && window.PluginAPI.batch) {
                            // PluginAPI.batch returns already-parsed JSON objects
                            try {
                                const results = await window.PluginAPI.batch([
                                    {endpoint: `/plugins/config?plugin_id=${pluginId}`, method: 'GET'},
                                    {endpoint: `/plugins/schema?plugin_id=${pluginId}`, method: 'GET'},
                                    {endpoint: '/plugins/installed', method: 'GET'}
                                ]);
                                [configData, schemaData, pluginsData] = results;
                            } catch (batchError) {
                                console.error('Batch API request failed, falling back to individual requests:', batchError);
                                // Fall back to individual requests
                                const [configResponse, schemaResponse, pluginsResponse] = await Promise.all([
                                    fetch(`/api/v3/plugins/config?plugin_id=${pluginId}`).then(r => r.json()).catch(e => ({ status: 'error', message: e.message })),
                                    fetch(`/api/v3/plugins/schema?plugin_id=${pluginId}`).then(r => r.json()).catch(e => ({ status: 'error', message: e.message })),
                                    fetch(`/api/v3/plugins/installed`).then(r => r.json()).catch(e => ({ status: 'error', message: e.message }))
                                ]);
                                configData = configResponse;
                                schemaData = schemaResponse;
                                pluginsData = pluginsResponse;
                            }
                        } else {
                            // Direct fetch returns Response objects that need parsing
                            const [configResponse, schemaResponse, pluginsResponse] = await Promise.all([
                                fetch(`/api/v3/plugins/config?plugin_id=${pluginId}`).then(r => r.json()).catch(e => ({ status: 'error', message: e.message })),
                                fetch(`/api/v3/plugins/schema?plugin_id=${pluginId}`).then(r => r.json()).catch(e => ({ status: 'error', message: e.message })),
                                fetch(`/api/v3/plugins/installed`).then(r => r.json()).catch(e => ({ status: 'error', message: e.message }))
                            ]);
                            configData = configResponse;
                            schemaData = schemaResponse;
                            pluginsData = pluginsResponse;
                        }

                        if (configData && configData.status === 'success') {
                            this.config = configData.data;
                        } else {
                            console.warn('Config API returned non-success status:', configData);
                            // Set defaults if config failed to load
                            this.config = { enabled: true, display_duration: 30 };
                        }

                        if (schemaData && schemaData.status === 'success') {
                            this.schema = schemaData.data.schema || {};
                        } else {
                            console.warn('Schema API returned non-success status:', schemaData);
                            // Set empty schema as fallback
                            this.schema = {};
                        }

                        // Extract web_ui_actions from installed plugins and update plugin data
                        if (pluginsData && pluginsData.status === 'success' && pluginsData.data && pluginsData.data.plugins) {
                            // Update window.installedPlugins with fresh data (includes commit info)
                            // The setter will check if data actually changed before updating tabs
                            window.installedPlugins = pluginsData.data.plugins;
                            // Update Alpine.js app data
                            this.installedPlugins = pluginsData.data.plugins;
                            
                            const pluginInfo = pluginsData.data.plugins.find(p => p.id === pluginId);
                            this.webUiActions = pluginInfo ? (pluginInfo.web_ui_actions || []) : [];
                            debugLog('[DEBUG] Loaded web_ui_actions for', pluginId, ':', this.webUiActions.length, 'actions');
                            debugLog('[DEBUG] Updated plugin data with commit info:', pluginInfo ? {
                                last_commit: pluginInfo.last_commit,
                                branch: pluginInfo.branch,
                                last_updated: pluginInfo.last_updated
                            } : 'plugin not found');
                        } else {
                            console.warn('Plugins API returned non-success status:', pluginsData);
                            this.webUiActions = [];
                        }

                        debugLog('Loaded config, schema, and actions for', pluginId);
                    } catch (error) {
                        console.error('Error loading plugin config:', error);
                        this.config = { enabled: true, display_duration: 30 };
                        this.schema = {};
                        this.webUiActions = [];
                    } finally {
                        this.loading = false;
                    }
                },

                generateConfigForm(pluginId, config, schema, webUiActions = []) {
                    // Safety check - if schema/config not ready, return empty
                    if (!pluginId || !config) {
                        return '<div class="text-gray-500">Loading configuration...</div>';
                    }
                    
                    // Only log once per plugin to avoid spam (Alpine.js may call this multiple times during rendering)
                    if (!this._configFormLogged || this._configFormLogged !== pluginId) {
                        debugLog('[DEBUG] generateConfigForm called for', pluginId, 'with', webUiActions?.length || 0, 'actions');
                        // Debug: Check if image_config.images has x-widget in schema
                        if (schema && schema.properties && schema.properties.image_config) {
                            const imgConfig = schema.properties.image_config;
                            if (imgConfig.properties && imgConfig.properties.images) {
                                const imagesProp = imgConfig.properties.images;
                                debugLog('[DEBUG] Schema check - image_config.images:', {
                                    type: imagesProp.type,
                                    'x-widget': imagesProp['x-widget'],
                                    'has x-widget': 'x-widget' in imagesProp,
                                    keys: Object.keys(imagesProp)
                                });
                            }
                        }
                        this._configFormLogged = pluginId;
                    }
                    if (!schema || !schema.properties) {
                        return this.generateSimpleConfigForm(config, webUiActions, pluginId);
                    }

                    // Helper function to get schema property by full key path
                    const getSchemaProperty = (schemaObj, keyPath) => {
                        if (!schemaObj || !schemaObj.properties) return null;
                        const keys = keyPath.split('.');
                        let current = schemaObj.properties;
                        for (let i = 0; i < keys.length; i++) {
                            const k = keys[i];
                            if (!current || !current[k]) {
                                return null;
                            }
                            
                            const prop = current[k];
                            // If this is the last key, return the property
                            if (i === keys.length - 1) {
                                return prop;
                            }
                            
                            // If this property has nested properties, navigate deeper
                            if (prop && typeof prop === 'object' && prop.properties) {
                                current = prop.properties;
                            } else {
                                // Can't navigate deeper
                                return null;
                            }
                        }
                        return null;
                    };
                    
                    const generateFieldHtml = (key, prop, value, prefix = '') => {
                        const fullKey = prefix ? `${prefix}.${key}` : key;
                        const label = prop.title || key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                        const description = prop.description || '';
                        let html = '';
                        
                        // Debug: Log property structure for arrays to help diagnose file-upload widget issues
                        if (prop.type === 'array') {
                            // Also check schema directly as fallback
                            const schemaProp = getSchemaProperty(schema, fullKey);
                            const xWidgetFromSchema = schemaProp ? (schemaProp['x-widget'] || schemaProp['x_widget']) : null;
                            
                            debugLog('[DEBUG generateFieldHtml] Array property:', fullKey, {
                                'prop.x-widget': prop['x-widget'],
                                'prop.x_widget': prop['x_widget'],
                                'schema.x-widget': xWidgetFromSchema,
                                'hasOwnProperty(x-widget)': prop.hasOwnProperty('x-widget'),
                                'x-widget in prop': 'x-widget' in prop,
                                'all prop keys': Object.keys(prop),
                                'schemaProp keys': schemaProp ? Object.keys(schemaProp) : 'null'
                            });
                        }

                        // Handle nested objects
                        if (prop.type === 'object' && prop.properties) {
                            const sectionId = `section-${fullKey.replace(/\./g, '-')}`;
                            const nestedConfig = value || {};
                            const sectionLabel = prop.title || key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                            // Calculate nesting depth for better spacing
                            const nestingDepth = (fullKey.match(/\./g) || []).length;
                            const marginClass = nestingDepth > 1 ? 'mb-6' : 'mb-4';
                            
                            html += `
                                <div class="nested-section border border-gray-300 rounded-lg ${marginClass}">
                                    <button type="button" 
                                            class="w-full bg-gray-100 hover:bg-gray-200 px-4 py-3 flex items-center justify-between text-left transition-colors"
                                            onclick="toggleNestedSection('${sectionId}', event); return false;">
                                        <div class="flex-1">
                                            <h4 class="font-semibold text-gray-900">${sectionLabel}</h4>
                                            ${description ? `<p class="text-sm text-gray-600 mt-1">${description}</p>` : ''}
                                        </div>
                                        <i id="${sectionId}-icon" class="fas fa-chevron-right text-gray-500 transition-transform"></i>
                                    </button>
                                    <div id="${sectionId}" class="nested-content collapsed bg-gray-50 px-4 py-4 space-y-3" style="max-height: 0; display: none;">
                            `;
                            
                            // Recursively generate fields for nested properties
                            // Get ordered properties if x-propertyOrder is defined
                            let nestedPropertyEntries = Object.entries(prop.properties);
                            if (prop['x-propertyOrder'] && Array.isArray(prop['x-propertyOrder'])) {
                                const order = prop['x-propertyOrder'];
                                const orderedEntries = [];
                                const unorderedEntries = [];
                                
                                // Separate ordered and unordered properties
                                nestedPropertyEntries.forEach(([nestedKey, nestedProp]) => {
                                    const index = order.indexOf(nestedKey);
                                    if (index !== -1) {
                                        orderedEntries[index] = [nestedKey, nestedProp];
                                    } else {
                                        unorderedEntries.push([nestedKey, nestedProp]);
                                    }
                                });
                                
                                // Combine ordered entries (filter out undefined from sparse array) with unordered entries
                                nestedPropertyEntries = orderedEntries.filter(entry => entry !== undefined).concat(unorderedEntries);
                            }
                            
                            nestedPropertyEntries.forEach(([nestedKey, nestedProp]) => {
                                // Use config value if it exists and is not null (including false), otherwise use schema default
                                // Check if key exists in config and value is not null/undefined
                                const hasValue = nestedKey in nestedConfig && nestedConfig[nestedKey] !== null && nestedConfig[nestedKey] !== undefined;
                                // For nested objects, if the value is an empty object, still use it (don't fall back to default)
                                const isNestedObject = nestedProp.type === 'object' && nestedProp.properties;
                                const nestedValue = hasValue ? nestedConfig[nestedKey] : 
                                    (nestedProp.default !== undefined ? nestedProp.default : 
                                     (isNestedObject ? {} : (nestedProp.type === 'array' ? [] : (nestedProp.type === 'boolean' ? false : ''))));
                                
                                // Debug logging for file-upload widgets
                                if (nestedProp.type === 'array' && (nestedProp['x-widget'] === 'file-upload' || nestedProp['x_widget'] === 'file-upload')) {
                                    debugLog('[DEBUG] Found file-upload widget in nested property:', nestedKey, 'fullKey:', fullKey + '.' + nestedKey, 'prop:', nestedProp);
                                }
                                
                                html += generateFieldHtml(nestedKey, nestedProp, nestedValue, fullKey);
                            });
                            
                            html += `
                                    </div>
                                </div>
                            `;
                            
                            // Add extra spacing after nested sections to prevent overlap with next section
                            if (nestingDepth > 0) {
                                html += `<div class="mb-2"></div>`;
                            }
                            
                            return html;
                        }

                        // Regular (non-nested) field
                        html += `<div class="form-group">`;
                        html += `<label class="block text-sm font-medium text-gray-700 mb-1">${label}</label>`;

                        if (description) {
                            html += `<p class="text-sm text-gray-600 mb-2">${description}</p>`;
                        }

                        // Generate appropriate input based on type
                        if (prop.type === 'boolean') {
                            html += `<label class="flex items-center">`;
                            html += `<input type="checkbox" name="${fullKey}" ${value ? 'checked' : ''} class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">`;
                            html += `<span class="ml-2 text-sm">Enabled</span>`;
                            html += `</label>`;
                        } else if (prop.type === 'number' || prop.type === 'integer' || 
                                   (Array.isArray(prop.type) && (prop.type.includes('number') || prop.type.includes('integer')))) {
                            // Handle union types like ["integer", "null"]
                            const isUnionType = Array.isArray(prop.type);
                            const allowsNull = isUnionType && prop.type.includes('null');
                            const isInteger = prop.type === 'integer' || (isUnionType && prop.type.includes('integer'));
                            const isNumber = prop.type === 'number' || (isUnionType && prop.type.includes('number'));
                            const min = prop.minimum !== undefined ? `min="${prop.minimum}"` : '';
                            const max = prop.maximum !== undefined ? `max="${prop.maximum}"` : '';
                            const step = isInteger ? 'step="1"' : 'step="any"';
                            
                            // For union types with null, don't show default if value is null (leave empty)
                            // This allows users to explicitly set null by leaving it empty
                            let fieldValue = '';
                            if (value !== undefined && value !== null) {
                                fieldValue = value;
                            } else if (!allowsNull && prop.default !== undefined) {
                                // Only use default if null is not allowed
                                fieldValue = prop.default;
                            }
                            
                            // Ensure value respects min/max constraints
                            if (fieldValue !== '' && fieldValue !== undefined && fieldValue !== null) {
                                const numValue = typeof fieldValue === 'string' ? parseFloat(fieldValue) : fieldValue;
                                if (!isNaN(numValue)) {
                                    // Clamp value to min/max if constraints exist
                                    if (prop.minimum !== undefined && numValue < prop.minimum) {
                                        fieldValue = prop.minimum;
                                    } else if (prop.maximum !== undefined && numValue > prop.maximum) {
                                        fieldValue = prop.maximum;
                                    } else {
                                        fieldValue = numValue;
                                    }
                                }
                            }
                            
                            // Add placeholder/help text for null-able fields
                            const placeholder = allowsNull ? 'Leave empty to use current time (random)' : '';
                            const helpText = allowsNull && description && description.includes('null') ? 
                                `<p class="text-xs text-gray-500 mt-1">${description}</p>` : '';
                            
                            html += `<input type="number" name="${fullKey}" value="${fieldValue}" ${min} ${max} ${step} placeholder="${placeholder}" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">`;
                            if (helpText) {
                                html += helpText;
                            }
                        } else if (prop.type === 'array') {
                            // AGGRESSIVE file upload widget detection
                            // For 'images' field in static-image plugin, always check schema directly
                            let isFileUpload = false;
                            let uploadConfig = {};
                            
                            // Direct check: if this is the 'images' field and schema has it with x-widget
                            if (fullKey === 'images' && schema && schema.properties && schema.properties.images) {
                                const imagesSchema = schema.properties.images;
                                if (imagesSchema['x-widget'] === 'file-upload' || imagesSchema['x_widget'] === 'file-upload') {
                                    isFileUpload = true;
                                    uploadConfig = imagesSchema['x-upload-config'] || imagesSchema['x_upload_config'] || {};
                                    debugLog('[DEBUG] ✅ Direct detection: images field has file-upload widget', uploadConfig);
                                }
                            }
                            
                            // Fallback: check prop object (should have x-widget if schema loaded correctly)
                            if (!isFileUpload) {
                                const xWidgetFromProp = prop['x-widget'] || prop['x_widget'] || prop.xWidget;
                                if (xWidgetFromProp === 'file-upload') {
                                    isFileUpload = true;
                                    uploadConfig = prop['x-upload-config'] || prop['x_upload_config'] || {};
                                    debugLog('[DEBUG] ✅ Detection via prop object');
                                }
                            }
                            
                            // Fallback: schema property lookup
                            if (!isFileUpload) {
                                let schemaProp = getSchemaProperty(schema, fullKey);
                                if (!schemaProp && fullKey === 'images' && schema && schema.properties && schema.properties.images) {
                                    schemaProp = schema.properties.images;
                                }
                                const xWidgetFromSchema = schemaProp ? (schemaProp['x-widget'] || schemaProp['x_widget']) : null;
                                if (xWidgetFromSchema === 'file-upload') {
                                    isFileUpload = true;
                                    uploadConfig = schemaProp['x-upload-config'] || schemaProp['x_upload_config'] || {};
                                    debugLog('[DEBUG] ✅ Detection via schema lookup');
                                }
                            }
                            
                            // Debug logging for ALL array fields to diagnose
                            debugLog('[DEBUG] Array field check:', fullKey, {
                                'isFileUpload': isFileUpload,
                                'prop keys': Object.keys(prop),
                                'prop.x-widget': prop['x-widget'],
                                'schema.properties.images exists': !!(schema && schema.properties && schema.properties.images),
                                'schema.properties.images.x-widget': (schema && schema.properties && schema.properties.images) ? schema.properties.images['x-widget'] : null,
                                'uploadConfig': uploadConfig
                            });
                            
                            if (isFileUpload) {
                                debugLog('[DEBUG] ✅ Rendering file-upload widget for', fullKey, 'with config:', uploadConfig);
                                // Use the file upload widget from plugins.html
                                // We'll need to call a function that exists in the global scope
                                const maxFiles = uploadConfig.max_files || 10;
                                const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/bmp', 'image/gif'];
                                const maxSizeMB = uploadConfig.max_size_mb || 5;
                                
                                const currentImages = Array.isArray(value) ? value : [];
                                const fieldId = fullKey.replace(/\./g, '_');
                                const safePluginId = (uploadConfig.plugin_id || pluginId || 'static-image').toString().replace(/[^a-zA-Z0-9_-]/g, '_');
                                
                                html += `
                                    <div id="${fieldId}_upload_widget" class="mt-1">
                                        <!-- File Upload Drop Zone -->
                                        <div id="${fieldId}_drop_zone" 
                                             class="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-blue-400 transition-colors cursor-pointer"
                                             ondrop="window.handleFileDrop(event, this.dataset.fieldId)" 
                                             ondragover="event.preventDefault()" 
                                             data-field-id="${fieldId}"
                                             onclick="document.getElementById(this.dataset.fieldId + '_file_input').click()">
                                            <input type="file" 
                                                   id="${fieldId}_file_input" 
                                                   multiple 
                                                   accept="${allowedTypes.join(',')}"
                                                   style="display: none;"
                                                   data-field-id="${fieldId}"
                                                   onchange="window.handleFileSelect(event, this.dataset.fieldId)">
                                            <i class="fas fa-cloud-upload-alt text-3xl text-gray-400 mb-2"></i>
                                            <p class="text-sm text-gray-600">Drag and drop images here or click to browse</p>
                                            <p class="text-xs text-gray-500 mt-1">Max ${maxFiles} files, ${maxSizeMB}MB each (PNG, JPG, GIF, BMP)</p>
                                        </div>
                                        
                                        <!-- Uploaded Images List -->
                                        <div id="${fieldId}_image_list" class="mt-4 space-y-2">
                                            ${currentImages.map((img, idx) => {
                                                const imgSchedule = img.schedule || {};
                                                const hasSchedule = imgSchedule.enabled && imgSchedule.mode && imgSchedule.mode !== 'always';
                                                let scheduleSummary = 'Always shown';
                                                if (hasSchedule && window.getScheduleSummary) {
                                                    try {
                                                        scheduleSummary = window.getScheduleSummary(imgSchedule) || 'Scheduled';
                                                    } catch (e) {
                                                        scheduleSummary = 'Scheduled';
                                                    }
                                                } else if (hasSchedule) {
                                                    scheduleSummary = 'Scheduled';
                                                }
                                                // Escape the summary for HTML
                                                scheduleSummary = String(scheduleSummary).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
                                                
                                                return `
                                                <div id="img_${(img.id || idx).toString().replace(/[^a-zA-Z0-9_-]/g, '_')}" class="bg-gray-50 p-3 rounded-lg border border-gray-200">
                                                    <div class="flex items-center justify-between mb-2">
                                                        <div class="flex items-center space-x-3 flex-1">
                                                            <img src="/${(img.path || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')}" 
                                                                 alt="${(img.filename || '').replace(/"/g, '&quot;')}" 
                                                                 class="w-16 h-16 object-cover rounded"
                                                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                                                            <div style="display:none;" class="w-16 h-16 bg-gray-200 rounded flex items-center justify-center">
                                                                <i class="fas fa-image text-gray-400"></i>
                                                            </div>
                                                            <div class="flex-1 min-w-0">
                                                                <p class="text-sm font-medium text-gray-900 truncate">${String(img.original_filename || img.filename || 'Image').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>
                                                                <p class="text-xs text-gray-500">${img.size ? (Math.round(img.size / 1024) + ' KB') : ''} • ${(img.uploaded_at || '').replace(/&/g, '&amp;')}</p>
                                                                <p class="text-xs text-blue-600 mt-1">
                                                                    <i class="fas fa-clock mr-1"></i>${scheduleSummary}
                                                                </p>
                                                            </div>
                                                        </div>
                                                        <div class="flex items-center space-x-2 ml-4">
                                                            <button type="button" 
                                                                    data-field-id="${fieldId}"
                                                                    data-image-id="${img.id || ''}"
                                                                    data-image-idx="${idx}"
                                                                    onclick="window.openImageSchedule(this.dataset.fieldId, this.dataset.imageId || null, parseInt(this.dataset.imageIdx))"
                                                                    class="text-blue-600 hover:text-blue-800 p-2" 
                                                                    title="Schedule this image">
                                                                <i class="fas fa-calendar-alt"></i>
                                                            </button>
                                                            <button type="button" 
                                                                    data-field-id="${fieldId}"
                                                                    data-image-id="${img.id || ''}"
                                                                    data-plugin-id="${safePluginId}"
                                                                    onclick="window.deleteUploadedImage(this.dataset.fieldId, this.dataset.imageId, this.dataset.pluginId)"
                                                                    class="text-red-600 hover:text-red-800 p-2"
                                                                    title="Delete image">
                                                                <i class="fas fa-trash"></i>
                                                            </button>
                                                        </div>
                                                    </div>
                                                    <!-- Schedule widget will be inserted here when opened -->
                                                    <div id="schedule_${(img.id || idx).toString().replace(/[^a-zA-Z0-9_-]/g, '_')}" class="hidden mt-3 pt-3 border-t border-gray-300"></div>
                                                </div>
                                                `;
                                            }).join('')}
                                        </div>
                                        
                                        <!-- Hidden input to store image data -->
                                        <input type="hidden" id="${fieldId}_images_data" name="${fullKey}" value="${JSON.stringify(currentImages).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')}">
                                    </div>
                                `;
                            } else {
                                // Regular array input
                                const arrayValue = Array.isArray(value) ? value.join(', ') : '';
                                html += `<input type="text" name="${fullKey}" value="${arrayValue}" placeholder="Enter values separated by commas" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">`;
                                html += `<p class="text-sm text-gray-600 mt-1">Enter values separated by commas</p>`;
                            }
                        } else if (prop.enum) {
                            html += `<select name="${fullKey}" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">`;
                            prop.enum.forEach(option => {
                                const selected = value === option ? 'selected' : '';
                                html += `<option value="${option}" ${selected}>${option}</option>`;
                            });
                            html += `</select>`;
                        } else if (prop.type === 'string' && prop['x-widget'] === 'file-upload') {
                            // File upload widget for string fields (e.g., credentials.json)
                            const uploadConfig = prop['x-upload-config'] || {};
                            const uploadEndpoint = uploadConfig.upload_endpoint || '/api/v3/plugins/assets/upload';
                            const maxSizeMB = uploadConfig.max_size_mb || 1;
                            const allowedExtensions = uploadConfig.allowed_extensions || ['.json'];
                            const targetFilename = uploadConfig.target_filename || 'file.json';
                            const fieldId = fullKey.replace(/\./g, '_');
                            const hasFile = value && value !== '';
                            
                            html += `
                                <div id="${fieldId}_upload_widget" class="mt-1">
                                    <div id="${fieldId}_file_upload" 
                                         class="border-2 border-dashed border-gray-300 rounded-lg p-4 text-center hover:border-blue-400 transition-colors cursor-pointer"
                                         onclick="document.getElementById('${fieldId}_file_input').click()">
                                        <input type="file" 
                                               id="${fieldId}_file_input" 
                                               accept="${allowedExtensions.join(',')}"
                                               style="display: none;"
                                               data-field-id="${fieldId}"
                                               data-upload-endpoint="${uploadEndpoint}"
                                               data-target-filename="${targetFilename}"
                                               onchange="window.handleCredentialsUpload(event, this.dataset.fieldId, this.dataset.uploadEndpoint, this.dataset.targetFilename)">
                                        <i class="fas fa-file-upload text-2xl text-gray-400 mb-2"></i>
                                        <p class="text-sm text-gray-600" id="${fieldId}_status">
                                            ${hasFile ? `Current file: ${value}` : 'Click to upload ' + targetFilename}
                                        </p>
                                        <p class="text-xs text-gray-500 mt-1">Max ${maxSizeMB}MB (${allowedExtensions.join(', ')})</p>
                                    </div>
                                    <input type="hidden" name="${fullKey}" value="${value || ''}" id="${fieldId}_hidden">
                                </div>
                            `;
                        } else {
                            // Default to text input
                            const maxLength = prop.maxLength || '';
                            const maxLengthAttr = maxLength ? `maxlength="${maxLength}"` : '';
                            html += `<input type="text" name="${fullKey}" value="${value !== undefined ? value : ''}" ${maxLengthAttr} class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">`;
                        }

                        html += `</div>`;
                        return html;
                    };

                    let formHtml = '';
                    // Get ordered properties if x-propertyOrder is defined
                    let propertyEntries = Object.entries(schema.properties);
                    if (schema['x-propertyOrder'] && Array.isArray(schema['x-propertyOrder'])) {
                        const order = schema['x-propertyOrder'];
                        const orderedEntries = [];
                        const unorderedEntries = [];
                        
                        // Separate ordered and unordered properties
                        propertyEntries.forEach(([key, prop]) => {
                            const index = order.indexOf(key);
                            if (index !== -1) {
                                orderedEntries[index] = [key, prop];
                            } else {
                                unorderedEntries.push([key, prop]);
                            }
                        });
                        
                        // Combine ordered entries (filter out undefined from sparse array) with unordered entries
                        propertyEntries = orderedEntries.filter(entry => entry !== undefined).concat(unorderedEntries);
                    }
                    
                    propertyEntries.forEach(([key, prop]) => {
                        // Skip the 'enabled' property - it's managed separately via the header toggle
                        if (key === 'enabled') return;
                        // Use config value if key exists and is not null/undefined, otherwise use schema default
                        // Check if key exists in config and value is not null/undefined
                        const hasValue = key in config && config[key] !== null && config[key] !== undefined;
                        // For nested objects, if the value is an empty object, still use it (don't fall back to default)
                        const isNestedObject = prop.type === 'object' && prop.properties;
                        const value = hasValue ? config[key] : 
                            (prop.default !== undefined ? prop.default : 
                             (isNestedObject ? {} : (prop.type === 'array' ? [] : (prop.type === 'boolean' ? false : ''))));
                        formHtml += generateFieldHtml(key, prop, value);
                    });

                    // Add web UI actions section if plugin defines any
                    if (webUiActions && webUiActions.length > 0) {
                        debugLog('[DEBUG] Rendering', webUiActions.length, 'actions in tab form');
                        
                        // Map color names to explicit Tailwind classes
                        const colorMap = {
                            'blue': { bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-900', textLight: 'text-blue-700', btn: 'bg-blue-600 hover:bg-blue-700' },
                            'green': { bg: 'bg-green-50', border: 'border-green-200', text: 'text-green-900', textLight: 'text-green-700', btn: 'bg-green-600 hover:bg-green-700' },
                            'red': { bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-900', textLight: 'text-red-700', btn: 'bg-red-600 hover:bg-red-700' },
                            'yellow': { bg: 'bg-yellow-50', border: 'border-yellow-200', text: 'text-yellow-900', textLight: 'text-yellow-700', btn: 'bg-yellow-600 hover:bg-yellow-700' },
                            'purple': { bg: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-900', textLight: 'text-purple-700', btn: 'bg-purple-600 hover:bg-purple-700' }
                        };
                        
                        formHtml += `
                            <div class="border-t border-gray-200 pt-4 mt-4">
                                <h3 class="text-lg font-semibold text-gray-900 mb-3">Actions</h3>
                                <p class="text-sm text-gray-600 mb-4">${webUiActions[0].section_description || 'Perform actions for this plugin'}</p>
                                
                                <div class="space-y-3">
                        `;
                        
                        webUiActions.forEach((action, index) => {
                            const actionId = `action-${action.id}-${index}`;
                            const statusId = `action-status-${action.id}-${index}`;
                            const bgColor = action.color || 'blue';
                            const colors = colorMap[bgColor] || colorMap['blue'];
                            // Ensure pluginId is valid for template interpolation
                            const safePluginId = pluginId || '';
                            
                            formHtml += `
                                    <div class="${colors.bg} border ${colors.border} rounded-lg p-4">
                                        <div class="flex items-center justify-between">
                                            <div class="flex-1">
                                                <h4 class="font-medium ${colors.text} mb-1">
                                                    ${action.icon ? `<i class="${action.icon} mr-2"></i>` : ''}${action.title || action.id}
                                                </h4>
                                                <p class="text-sm ${colors.textLight}">${action.description || ''}</p>
                                            </div>
                                            <button type="button" 
                                                    id="${actionId}"
                                                    onclick="executePluginAction('${action.id}', ${index}, '${safePluginId}')" 
                                                    data-plugin-id="${safePluginId}"
                                                    data-action-id="${action.id}"
                                                    class="btn ${colors.btn} text-white px-4 py-2 rounded-md whitespace-nowrap">
                                                ${action.icon ? `<i class="${action.icon} mr-2"></i>` : ''}${action.button_text || action.title || 'Execute'}
                                            </button>
                                        </div>
                                        <div id="${statusId}" class="mt-3 hidden"></div>
                                    </div>
                            `;
                        });
                        
                        formHtml += `
                                </div>
                            </div>
                        `;
                    }

                    return formHtml;
                },

                generateSimpleConfigForm(config, webUiActions = [], pluginId = '') {
                    let actionsHtml = '';
                    if (webUiActions && webUiActions.length > 0) {
                        const colorMap = {
                            'blue': { bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-900', textLight: 'text-blue-700', btn: 'bg-blue-600 hover:bg-blue-700' },
                            'green': { bg: 'bg-green-50', border: 'border-green-200', text: 'text-green-900', textLight: 'text-green-700', btn: 'bg-green-600 hover:bg-green-700' },
                            'red': { bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-900', textLight: 'text-red-700', btn: 'bg-red-600 hover:bg-red-700' },
                            'yellow': { bg: 'bg-yellow-50', border: 'border-yellow-200', text: 'text-yellow-900', textLight: 'text-yellow-700', btn: 'bg-yellow-600 hover:bg-yellow-700' },
                            'purple': { bg: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-900', textLight: 'text-purple-700', btn: 'bg-purple-600 hover:bg-purple-700' }
                        };
                        
                        actionsHtml = `
                            <div class="border-t border-gray-200 pt-4 mt-4">
                                <h3 class="text-lg font-semibold text-gray-900 mb-3">Actions</h3>
                                <div class="space-y-3">
                        `;
                        webUiActions.forEach((action, index) => {
                            const actionId = `action-${action.id}-${index}`;
                            const statusId = `action-status-${action.id}-${index}`;
                            const bgColor = action.color || 'blue';
                            const colors = colorMap[bgColor] || colorMap['blue'];
                            // Ensure pluginId is valid for template interpolation
                            const safePluginId = pluginId || '';
                            actionsHtml += `
                                    <div class="${colors.bg} border ${colors.border} rounded-lg p-4">
                                        <div class="flex items-center justify-between">
                                            <div class="flex-1">
                                                <h4 class="font-medium ${colors.text} mb-1">
                                                    ${action.icon ? `<i class="${action.icon} mr-2"></i>` : ''}${action.title || action.id}
                                                </h4>
                                                <p class="text-sm ${colors.textLight}">${action.description || ''}</p>
                                            </div>
                                            <button type="button" 
                                                    id="${actionId}"
                                                    onclick="executePluginAction('${action.id}', ${index}, '${safePluginId}')" 
                                                    data-plugin-id="${safePluginId}"
                                                    data-action-id="${action.id}"
                                                    class="btn ${colors.btn} text-white px-4 py-2 rounded-md">
                                                ${action.icon ? `<i class="${action.icon} mr-2"></i>` : ''}${action.button_text || action.title || 'Execute'}
                                            </button>
                                        </div>
                                        <div id="${statusId}" class="mt-3 hidden"></div>
                                    </div>
                            `;
                        });
                        actionsHtml += `
                                </div>
                            </div>
                        `;
                    }
                    
                    return `
                        <div class="form-group">
                            <label class="block text-sm font-medium text-gray-700 mb-1">Display Duration (seconds)</label>
                            <input type="number" name="display_duration" value="${Math.max(5, Math.min(300, config.display_duration || 30))}" min="5" max="300" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                            <p class="text-sm text-gray-600 mt-1">How long to show this plugin's content</p>
                        </div>
                        ${actionsHtml}
                    `;
                },

                // Helper function to get schema property type for a field path
                getSchemaPropertyType(schema, path) {
                    if (!schema || !schema.properties) return null;
                    
                    const parts = path.split('.');
                    let current = schema.properties;
                    
                    for (let i = 0; i < parts.length; i++) {
                        const part = parts[i];
                        if (current && current[part]) {
                            if (i === parts.length - 1) {
                                return current[part];
                            } else if (current[part].properties) {
                                current = current[part].properties;
                            } else {
                                return null;
                            }
                        } else {
                            return null;
                        }
                    }
                    return null;
                },

                // Helper function to escape CSS selector special characters
                escapeCssSelector(str) {
                    if (typeof str !== 'string') {
                        str = String(str);
                    }
                    // Use CSS.escape() when available (handles unicode, leading digits, and edge cases)
                    if (typeof CSS !== 'undefined' && CSS.escape) {
                        return CSS.escape(str);
                    }
                    // Fallback to regex-based escaping for older browsers
                    // First, handle leading digits and whitespace (must be done before regex)
                    let escaped = str;
                    let hasLeadingHexEscape = false;
                    if (escaped.length > 0) {
                        const firstChar = escaped[0];
                        const firstCode = firstChar.charCodeAt(0);
                        
                        // Escape leading digit (0-9: U+0030-U+0039)
                        if (firstCode >= 0x30 && firstCode <= 0x39) {
                            const hex = firstCode.toString(16).toUpperCase().padStart(4, '0');
                            escaped = '\\' + hex + ' ' + escaped.slice(1);
                            hasLeadingHexEscape = true;
                        }
                        // Escape leading whitespace (space: U+0020, tab: U+0009, etc.)
                        else if (/\s/.test(firstChar)) {
                            const hex = firstCode.toString(16).toUpperCase().padStart(4, '0');
                            escaped = '\\' + hex + ' ' + escaped.slice(1);
                            hasLeadingHexEscape = true;
                        }
                    }
                    
                    // Escape special characters
                    escaped = escaped.replace(/[!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~]/g, '\\$&');
                    
                    // Escape internal spaces (replace spaces with \ ), but preserve space in hex escape
                    if (hasLeadingHexEscape) {
                        // Skip the first 6 characters (e.g., "\0030 ") when replacing spaces
                        escaped = escaped.slice(0, 6) + escaped.slice(6).replace(/ /g, '\\ ');
                    } else {
                        escaped = escaped.replace(/ /g, '\\ ');
                    }
                    
                    return escaped;
                },

                async savePluginConfig(pluginId, event) {
                    try {
                        // Get the form element for this plugin
                        const form = event ? event.target : null;
                        if (!form) {
                            throw new Error('Form element not found');
                        }
                        const formData = new FormData(form);
                        const schema = this.schema || {};
                        
                        // First, collect all checkbox states (including unchecked ones)
                        // Unchecked checkboxes don't appear in FormData, so we need to iterate form elements
                        const flatConfig = {};
                        
                        // Process all form elements to capture all field states
                        for (let i = 0; i < form.elements.length; i++) {
                            const element = form.elements[i];
                            const name = element.name;
                            
                            // Skip elements without names or submit buttons
                            if (!name || element.type === 'submit' || element.type === 'button') {
                                continue;
                            }
                            
                            // Handle checkboxes explicitly (both checked and unchecked)
                            if (element.type === 'checkbox') {
                                // Check if this is a checkbox group (name ends with [])
                                if (name.endsWith('[]')) {
                                    const baseName = name.slice(0, -2); // Remove '[]' suffix
                                    if (!flatConfig[baseName]) {
                                        flatConfig[baseName] = [];
                                    }
                                    if (element.checked) {
                                        flatConfig[baseName].push(element.value);
                                    }
                                } else {
                                    // Regular checkbox (boolean)
                                    flatConfig[name] = element.checked;
                                }
                            }
                            // Handle radio buttons
                            else if (element.type === 'radio') {
                                if (element.checked) {
                                    flatConfig[name] = element.value;
                                }
                            }
                            // Handle select elements (including multi-select)
                            else if (element.tagName === 'SELECT') {
                                if (element.multiple) {
                                    // Multi-select: get all selected options
                                    const selectedValues = Array.from(element.selectedOptions).map(opt => opt.value);
                                    flatConfig[name] = selectedValues;
                                } else {
                                    // Single select: handled by FormData, but ensure it's captured
                                    if (!(name in flatConfig)) {
                                        flatConfig[name] = element.value;
                                    }
                                }
                            }
                            // Handle textarea
                            else if (element.tagName === 'TEXTAREA') {
                                // Textarea: handled by FormData, but ensure it's captured
                                if (!(name in flatConfig)) {
                                    flatConfig[name] = element.value;
                                }
                            }
                        }
                        
                        // Now process FormData for other field types
                        for (const [key, value] of formData.entries()) {
                            // Skip checkboxes - we already handled them above
                            // Use querySelector to reliably find element by name (handles dot notation)
                            const escapedKey = this.escapeCssSelector(key);
                            const element = form.querySelector(`[name="${escapedKey}"]`);
                            if (element && element.type === 'checkbox') {
                                // Also skip checkbox groups (name ends with [])
                                if (key.endsWith('[]')) {
                                    continue; // Already processed
                                }
                                continue; // Already processed
                            }
                            // Skip multi-select - we already handled them above
                            if (element && element.tagName === 'SELECT' && element.multiple) {
                                continue; // Already processed
                            }
                            
                            // Get schema property type if available
                            const propSchema = this.getSchemaPropertyType(schema, key);
                            const propType = propSchema ? propSchema.type : null;
                            
                            // Handle based on schema type or field name patterns
                            if (propType === 'array') {
                                // Check if this is a file upload widget (JSON array in hidden input)
                                if (propSchema && propSchema['x-widget'] === 'file-upload') {
                                    try {
                                        // Unescape HTML entities that were escaped when setting the value
                                        let unescapedValue = value;
                                        if (typeof value === 'string') {
                                            // Reverse the HTML escaping: &quot; -> ", &#39; -> ', &amp; -> &
                                            unescapedValue = value
                                                .replace(/&quot;/g, '"')
                                                .replace(/&#39;/g, "'")
                                                .replace(/&lt;/g, '<')
                                                .replace(/&gt;/g, '>')
                                                .replace(/&amp;/g, '&');
                                        }
                                        
                                        // Try to parse as JSON
                                        const jsonValue = JSON.parse(unescapedValue);
                                        if (Array.isArray(jsonValue)) {
                                            flatConfig[key] = jsonValue;
                                            debugLog(`File upload array field ${key}: parsed JSON array with ${jsonValue.length} items`);
                                        } else {
                                            // Fallback to empty array
                                            flatConfig[key] = [];
                                        }
                                    } catch (e) {
                                        console.warn(`Failed to parse JSON for file upload field ${key}:`, e, 'Value:', value);
                                        // Not valid JSON, use empty array or try comma-separated
                                        if (value && value.trim()) {
                                            // Try to unescape and parse again
                                            try {
                                                const unescaped = value
                                                    .replace(/&quot;/g, '"')
                                                    .replace(/&#39;/g, "'")
                                                    .replace(/&amp;/g, '&');
                                                const jsonValue = JSON.parse(unescaped);
                                                if (Array.isArray(jsonValue)) {
                                                    flatConfig[key] = jsonValue;
                                                } else {
                                                    flatConfig[key] = [];
                                                }
                                            } catch (e2) {
                                                // If still fails, try comma-separated or empty array
                                                const arrayValue = value.split(',').map(v => v.trim()).filter(v => v);
                                                flatConfig[key] = arrayValue.length > 0 ? arrayValue : [];
                                            }
                                        } else {
                                            flatConfig[key] = [];
                                        }
                                    }
                                } else {
                                    // Regular array: convert comma-separated string to array
                                    const arrayValue = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                                    flatConfig[key] = arrayValue;
                                }
                            } else if (propType === 'integer' || (Array.isArray(propType) && propType.includes('integer'))) {
                                // Handle union types - if null is allowed and value is empty, keep as empty string (backend will convert to null)
                                if (Array.isArray(propType) && propType.includes('null') && (!value || value.trim() === '')) {
                                    flatConfig[key] = ''; // Send empty string, backend will normalize to null
                                } else {
                                    const numValue = parseInt(value, 10);
                                    flatConfig[key] = isNaN(numValue) ? (propSchema && propSchema.default !== undefined ? propSchema.default : 0) : numValue;
                                }
                            } else if (propType === 'number' || (Array.isArray(propType) && propType.includes('number'))) {
                                // Handle union types - if null is allowed and value is empty, keep as empty string (backend will convert to null)
                                if (Array.isArray(propType) && propType.includes('null') && (!value || value.trim() === '')) {
                                    flatConfig[key] = ''; // Send empty string, backend will normalize to null
                                } else {
                                    const numValue = parseFloat(value);
                                    flatConfig[key] = isNaN(numValue) ? (propSchema && propSchema.default !== undefined ? propSchema.default : 0) : numValue;
                                }
                            } else if (propType === 'boolean') {
                                // Boolean from FormData (shouldn't happen for checkboxes, but handle it)
                                flatConfig[key] = value === 'on' || value === 'true' || value === true;
                            } else {
                                // String or other types
                                // Check if it's a number field by name pattern (fallback if no schema)
                                if (!propType && (key.includes('duration') || key.includes('interval') || 
                                    key.includes('timeout') || key.includes('teams') || key.includes('fps') ||
                                    key.includes('bits') || key.includes('nanoseconds') || key.includes('hz'))) {
                                    const numValue = parseFloat(value);
                                    if (!isNaN(numValue)) {
                                        flatConfig[key] = Number.isInteger(numValue) ? parseInt(value, 10) : numValue;
                                    } else {
                                        flatConfig[key] = value;
                                    }
                                } else {
                                    flatConfig[key] = value;
                                }
                            }
                        }
                        
                        // Handle unchecked checkboxes using schema (if available)
                        if (schema && schema.properties) {
                            const collectBooleanFields = (props, prefix = '') => {
                                const boolFields = [];
                                for (const [key, prop] of Object.entries(props)) {
                                    const fullKey = prefix ? `${prefix}.${key}` : key;
                                    if (prop.type === 'boolean') {
                                        boolFields.push(fullKey);
                                    } else if (prop.type === 'object' && prop.properties) {
                                        boolFields.push(...collectBooleanFields(prop.properties, fullKey));
                                    }
                                }
                                return boolFields;
                            };
                            
                            const allBoolFields = collectBooleanFields(schema.properties);
                            allBoolFields.forEach(key => {
                                // Only set to false if the field is completely missing from flatConfig
                                // Don't override existing false values - they're explicitly set by the user
                                if (!(key in flatConfig)) {
                                    flatConfig[key] = false;
                                }
                            });
                        }
                        
                        // Convert dot notation to nested object
                        const dotToNested = (obj) => {
                            const result = {};
                            for (const key in obj) {
                                const parts = key.split('.');
                                let current = result;
                                for (let i = 0; i < parts.length - 1; i++) {
                                    if (!current[parts[i]]) {
                                        current[parts[i]] = {};
                                    }
                                    current = current[parts[i]];
                                }
                                current[parts[parts.length - 1]] = obj[key];
                            }
                            return result;
                        };
                        
                        const config = dotToNested(flatConfig);
                        
                        // Save to backend
                        const response = await fetch('/api/v3/plugins/config', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                plugin_id: pluginId,
                                config: config
                            })
                        });
                        
                        let data;
                        try {
                            data = await response.json();
                        } catch (e) {
                            console.error('Failed to parse JSON response:', e);
                            console.error('Response status:', response.status, response.statusText);
                            console.error('Response text:', await response.text());
                            throw new Error(`Failed to parse server response: ${response.status} ${response.statusText}`);
                        }
                        
                        debugLog('Response status:', response.status, 'Response OK:', response.ok);
                        debugLog('Response data:', JSON.stringify(data, null, 2));
                        
                        if (!response.ok || data.status !== 'success') {
                            let errorMessage = data.message || 'Failed to save configuration';
                            if (data.validation_errors && Array.isArray(data.validation_errors)) {
                                console.error('Validation errors:', data.validation_errors);
                                errorMessage += '\n\nValidation errors:\n' + data.validation_errors.join('\n');
                            }
                            if (data.config_keys && data.schema_keys) {
                                console.error('Config keys sent:', data.config_keys);
                                console.error('Schema keys expected:', data.schema_keys);
                                const extraKeys = data.config_keys.filter(k => !data.schema_keys.includes(k));
                                const missingKeys = data.schema_keys.filter(k => !data.config_keys.includes(k));
                                if (extraKeys.length > 0) {
                                    errorMessage += '\n\nExtra keys (not in schema): ' + extraKeys.join(', ');
                                }
                                if (missingKeys.length > 0) {
                                    errorMessage += '\n\nMissing keys (in schema): ' + missingKeys.join(', ');
                                }
                            }
                            this.showNotification(errorMessage, 'error');
                            console.error('Config save failed - Full error response:', JSON.stringify(data, null, 2));
                        } else {
                            this.showNotification('Configuration saved successfully', 'success');
                            // Reload plugin config to reflect changes
                            await this.loadPluginConfig(pluginId);
                        }
                    } catch (error) {
                        console.error('Error saving plugin config:', error);
                        this.showNotification('Error saving configuration: ' + error.message, 'error');
                    }
                },

                formatCommitInfo(commit, branch) {
                    // Handle null, undefined, or empty string
                    const commitStr = (commit && String(commit).trim()) || '';
                    const branchStr = (branch && String(branch).trim()) || '';
                    
                    if (!commitStr && !branchStr) return 'Unknown';
                    
                    const shortCommit = commitStr.length >= 7 ? commitStr.substring(0, 7) : commitStr;

                    if (branchStr && shortCommit) {
                        return `${branchStr} · ${shortCommit}`;
                    }
                    if (branchStr) {
                        return branchStr;
                    }
                    if (shortCommit) {
                        return shortCommit;
                    }
                    return 'Unknown';
                },

                formatDateInfo(dateString) {
                    // Handle null, undefined, or empty string
                    if (!dateString || !String(dateString).trim()) return 'Unknown';
                    
                    try {
                        const date = new Date(dateString);
                        // Check if date is valid
                        if (isNaN(date.getTime())) {
                            return 'Unknown';
                        }
                        
                        const now = new Date();
                        const diffTime = Math.abs(now - date);
                        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
                        
                        if (diffDays < 1) {
                            return 'Today';
                        } else if (diffDays < 2) {
                            return 'Yesterday';
                        } else if (diffDays < 7) {
                            return `${diffDays} days ago`;
                        } else if (diffDays < 30) {
                            const weeks = Math.floor(diffDays / 7);
                            return `${weeks} ${weeks === 1 ? 'week' : 'weeks'} ago`;
                        } else {
                            // Return formatted date for older items
                            return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
                        }
                    } catch (e) {
                        console.error('Error formatting date:', e, dateString);
                        return 'Unknown';
                    }
                }
            };
            
            // Update window.app to return full implementation
            window.app = function() {
                return fullImplementation;
            };
            
            // If Alpine is already initialized, update the existing component immediately
            if (window.Alpine) {
                // Use requestAnimationFrame for immediate execution without blocking
                requestAnimationFrame(() => {
                    if (window._appEnhanced) return;
                    window._appEnhanced = true;
                    const isAPMode = window.location.hostname === '192.168.4.1' ||
                                   window.location.hostname.startsWith('192.168.4.');
                    const defaultTab = isAPMode ? 'wifi' : 'overview';
                    const appElement = document.querySelector('[x-data]');
                    if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                        const existingComponent = appElement._x_dataStack[0];
                        // Preserve runtime state that should not be reset
                        const preservedPlugins = existingComponent.installedPlugins;
                        const preservedTab = existingComponent.activeTab;
                        // Replace all properties and methods from full implementation
                        Object.keys(fullImplementation).forEach(key => {
                            existingComponent[key] = fullImplementation[key];
                        });
                        // Restore runtime state if non-default
                        if (preservedPlugins && preservedPlugins.length > 0) {
                            existingComponent.installedPlugins = preservedPlugins;
                        }
                        if (preservedTab && preservedTab !== defaultTab) {
                            existingComponent.activeTab = preservedTab;
                        }
                        // Call init to load plugins and set up watchers (only if not already initialized)
                        if (typeof existingComponent.init === 'function' && !existingComponent._initialized) {
                            existingComponent.init();
                        }
                    }
                });
            }
            
            return fullImplementation;
        }
        
        // Make app() available globally
        window.app = app;


        // ===== Nested Section Toggle =====
        window.toggleNestedSection = function(sectionId, event) {
            // Prevent event bubbling if event is provided
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            
            const content = document.getElementById(sectionId);
            const icon = document.getElementById(sectionId + '-icon');
            
            if (!content || !icon) {
                console.warn('[toggleNestedSection] Content or icon not found for:', sectionId);
                return;
            }
            
            // Check if content is currently collapsed (has 'collapsed' class or display:none)
            const isCollapsed = content.classList.contains('collapsed') || 
                                content.style.display === 'none' ||
                                (content.style.display === '' && !content.classList.contains('expanded'));
            
            if (isCollapsed) {
                // Expand the section
                content.classList.remove('collapsed');
                content.classList.add('expanded');
                content.style.display = 'block';
                content.style.overflow = 'hidden'; // Prevent content jumping during animation
                
                // CRITICAL FIX: Use setTimeout to ensure browser has time to layout the element
                // When element goes from display:none to display:block, scrollHeight might be 0
                // We need to wait for the browser to calculate the layout
                setTimeout(() => {
                    // Force reflow to ensure transition works
                    void content.offsetHeight;
                    
                    // Now measure the actual content height after layout
                    const scrollHeight = content.scrollHeight;
                    if (scrollHeight > 0) {
                        content.style.maxHeight = scrollHeight + 'px';
                    } else {
                        // Fallback: if scrollHeight is still 0, try measuring again after a brief delay
                        setTimeout(() => {
                            const retryHeight = content.scrollHeight;
                            content.style.maxHeight = retryHeight > 0 ? retryHeight + 'px' : '500px';
                        }, 10);
                    }
                }, 10);
                
                icon.classList.remove('fa-chevron-right');
                icon.classList.add('fa-chevron-down');
                
                // After animation completes, remove max-height constraint to allow natural expansion
                setTimeout(() => {
                    if (content.classList.contains('expanded') && !content.classList.contains('collapsed')) {
                        content.style.maxHeight = 'none';
                        content.style.overflow = '';
                    }
                }, 320); // Slightly longer than transition duration
            } else {
                // Collapse the section
                content.classList.add('collapsed');
                content.classList.remove('expanded');
                content.style.overflow = 'hidden'; // Prevent content jumping during animation
                
                // Set max-height to current scroll height first (required for smooth animation)
                const currentHeight = content.scrollHeight;
                content.style.maxHeight = currentHeight + 'px';
                
                // Force reflow to apply the height
                void content.offsetHeight;
                
                // Then animate to 0
                setTimeout(() => {
                    content.style.maxHeight = '0';
                }, 10);
                
                // Hide after transition completes
                setTimeout(() => {
                    if (content.classList.contains('collapsed')) {
                        content.style.display = 'none';
                        content.style.overflow = '';
                    }
                }, 320); // Match the CSS transition duration + small buffer
                
                icon.classList.remove('fa-chevron-down');
                icon.classList.add('fa-chevron-right');
            }
        };

        // ===== Display Preview Functions (from v2) =====
        
        function updateDisplayPreview(data) {
            const preview = document.getElementById('displayPreview');
            const stage = document.getElementById('previewStage');
            const img = document.getElementById('displayImage');
            const canvas = document.getElementById('gridOverlay');
            const ledCanvas = document.getElementById('ledCanvas');
            const placeholder = document.getElementById('displayPlaceholder');

            // Always cache the latest frame so the floating preview can show
            // something the moment it's opened — SSE only sends frames when
            // the display CHANGES, so a late subscriber would otherwise stare
            // at an empty panel until the next change.
            if (data.image) {
                window._lastPreviewFrame = data.image;
            }
            // Feed the floating mini preview (lives in base.html, present on
            // every tab) before the overview-only guard below.
            const floatImg = document.getElementById('floating-preview-img');
            const floatPanel = document.getElementById('floating-preview');
            if (floatImg && floatPanel && floatPanel.style.display !== 'none' && data.image) {
                floatImg.src = `data:image/png;base64,${data.image}`;
            }

            if (!stage || !img || !placeholder) return; // Not on overview page

            if (data.image) {
                // Show stage
                placeholder.style.display = 'none';
                stage.style.display = 'inline-block';

                // Current scale from slider
                const scale = parseInt(document.getElementById('scaleRange')?.value || '8');

                // Update image and meta label. Size everything from the PNG's
                // OWN dimensions (naturalWidth/Height) once it has loaded —
                // not from the server-reported config dimensions. If the
                // config disagrees with what the display service actually
                // renders (stale config, service not yet restarted), sizing
                // from config stretches the image at a fractional ratio and
                // the preview looks blurry until the scale slider forces a
                // re-render. The slider path always used natural dimensions;
                // now both paths do.
                img.style.imageRendering = 'pixelated';
                img.onload = () => {
                    const nw = img.naturalWidth || data.width || 128;
                    const nh = img.naturalHeight || data.height || 64;
                    const width = nw * scale;
                    const height = nh * scale;
                    img.style.width = width + 'px';
                    img.style.height = height + 'px';
                    ledCanvas.width = width;
                    ledCanvas.height = height;
                    canvas.width = width;
                    canvas.height = height;
                    const meta = document.getElementById('previewMeta');
                    if (meta) {
                        meta.textContent = `${nw} x ${nh} @ ${scale}x`;
                    }
                    drawGrid(canvas, nw, nh, scale);
                    renderLedDots();
                };
                img.src = `data:image/png;base64,${data.image}`;
            } else {
                stage.style.display = 'none';
                placeholder.style.display = 'block';
                placeholder.innerHTML = `<div class="text-center text-gray-400 py-8">
                    <i class="fas fa-exclamation-triangle text-4xl mb-3"></i>
                    <p>No display data available</p>
                </div>`;
            }
        }

        function renderLedDots() {
            const ledCanvas = document.getElementById('ledCanvas');
            const img = document.getElementById('displayImage');
            const toggle = document.getElementById('toggleLedDots');

            if (!ledCanvas || !img || !toggle) {
                return;
            }

            const show = toggle.checked;

            if (!show) {
                // LED mode OFF: Show image, hide canvas
                img.style.visibility = 'visible';
                ledCanvas.style.display = 'none';
                const ctx = ledCanvas.getContext('2d');
                ctx.clearRect(0, 0, ledCanvas.width, ledCanvas.height);
                return;
            }

            // LED mode ON: Hide image (but keep layout space), show only dots on canvas
            img.style.visibility = 'hidden';
            ledCanvas.style.display = 'block';

            const scale = parseInt(document.getElementById('scaleRange')?.value || '8');
            const fillPct = parseInt(document.getElementById('dotFillRange')?.value || '75');
            const dotRadius = Math.max(1, Math.floor((scale * fillPct) / 200)); // radius in px

            const ctx = ledCanvas.getContext('2d', { willReadFrequently: true });
            ctx.clearRect(0, 0, ledCanvas.width, ledCanvas.height);

            // Create an offscreen canvas to sample pixel colors
            const off = document.createElement('canvas');
            const logicalWidth = Math.floor(ledCanvas.width / scale);
            const logicalHeight = Math.floor(ledCanvas.height / scale);
            off.width = logicalWidth;
            off.height = logicalHeight;
            const offCtx = off.getContext('2d', { willReadFrequently: true });

            // Draw the current image scaled down to logical LEDs to sample colors
            try {
                offCtx.drawImage(img, 0, 0, logicalWidth, logicalHeight);
            } catch (e) {
                console.error('Failed to draw image to offscreen canvas:', e);
                return;
            }

            // Fill canvas with black background (LED matrix bezel)
            ctx.fillStyle = 'rgb(0, 0, 0)';
            ctx.fillRect(0, 0, ledCanvas.width, ledCanvas.height);

            // Read the whole frame once instead of one getImageData call per
            // pixel (a 192x48 panel would otherwise issue ~9,200 calls per
            // frame — noticeably heavy on phones).
            let frame;
            try {
                frame = offCtx.getImageData(0, 0, logicalWidth, logicalHeight).data;
            } catch (e) {
                console.error('Failed to read offscreen canvas pixels:', e);
                return;
            }

            // Draw circular dots for each LED pixel
            let drawn = 0;
            for (let y = 0; y < logicalHeight; y++) {
                for (let x = 0; x < logicalWidth; x++) {
                    const i = (y * logicalWidth + x) * 4;
                    const r = frame[i], g = frame[i + 1], b = frame[i + 2], a = frame[i + 3];

                    // Skip fully transparent or black pixels to reduce overdraw
                    if (a === 0 || (r|g|b) === 0) continue;

                    ctx.fillStyle = `rgb(${r},${g},${b})`;
                    const cx = Math.floor(x * scale + scale / 2);
                    const cy = Math.floor(y * scale + scale / 2);
                    ctx.beginPath();
                    ctx.arc(cx, cy, dotRadius, 0, Math.PI * 2);
                    ctx.fill();
                    drawn++;
                }
            }

            // If nothing was drawn (e.g., image not ready), hide overlay to show base image
            if (drawn === 0) {
                ledCanvas.style.display = 'none';
            }
        }

        function drawGrid(canvas, pixelWidth, pixelHeight, scale) {
            const toggle = document.getElementById('toggleGrid');
            if (!toggle || !toggle.checked) {
                const ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                return;
            }
            
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
            ctx.lineWidth = 1;
            
            for (let x = 0; x <= pixelWidth; x++) {
                ctx.beginPath();
                ctx.moveTo(x * scale, 0);
                ctx.lineTo(x * scale, pixelHeight * scale);
                ctx.stroke();
            }
            
            for (let y = 0; y <= pixelHeight; y++) {
                ctx.beginPath();
                ctx.moveTo(0, y * scale);
                ctx.lineTo(pixelWidth * scale, y * scale);
                ctx.stroke();
            }
        }

        function takeScreenshot() {
            const img = document.getElementById('displayImage');
            if (img && img.src) {
                const link = document.createElement('a');
                link.download = `led_matrix_${new Date().getTime()}.png`;
                link.href = img.src;
                link.click();
            }
        }

        // ===== Plugin Management Functions =====

        // Make togglePluginFromTab global so Alpine.js can access it  
        window.togglePluginFromTab = async function(pluginId, enabled) {
            try {
                const response = await fetch('/api/v3/plugins/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plugin_id: pluginId, enabled })
                });
                const data = await response.json();

                showNotification(data.message, data.status);

                if (data.status === 'success') {
                    // Update the plugin in window.installedPlugins
                    if (window.installedPlugins) {
                        const plugin = window.installedPlugins.find(p => p.id === pluginId);
                        if (plugin) {
                            plugin.enabled = enabled;
                        }
                    }
                    
                    // Refresh the plugin list to ensure both management page and config page stay in sync
                    if (typeof loadInstalledPlugins === 'function') {
                        loadInstalledPlugins();
                    }
                } else {
                    // Revert the toggle if API call failed
                    if (window.installedPlugins) {
                        const plugin = window.installedPlugins.find(p => p.id === pluginId);
                        if (plugin) {
                            plugin.enabled = !enabled;
                        }
                    }
                }

            } catch (error) {
                showNotification('Error toggling plugin: ' + error.message, 'error');
                // Revert on error
                if (window.installedPlugins) {
                    const plugin = window.installedPlugins.find(p => p.id === pluginId);
                    if (plugin) {
                        plugin.enabled = !enabled;
                    }
                }
            }
        }

        // Helper function to get schema property type for a field path
        function getSchemaPropertyType(schema, path) {
            if (!schema || !schema.properties) return null;
            
            const parts = path.split('.');
            let current = schema.properties;
            
            for (let i = 0; i < parts.length; i++) {
                const part = parts[i];
                if (current && current[part]) {
                    if (i === parts.length - 1) {
                        return current[part];
                    } else if (current[part].properties) {
                        current = current[part].properties;
                    } else {
                        return null;
                    }
                } else {
                    return null;
                }
            }
            return null;
        }

        // Helper function to escape CSS selector special characters
        function escapeCssSelector(str) {
            if (typeof str !== 'string') {
                str = String(str);
            }
            // Use CSS.escape() when available (handles unicode, leading digits, and edge cases)
            if (typeof CSS !== 'undefined' && CSS.escape) {
                return CSS.escape(str);
            }
            // Fallback to regex-based escaping for older browsers
            // First, handle leading digits and whitespace (must be done before regex)
            let escaped = str;
            let hasLeadingHexEscape = false;
            if (escaped.length > 0) {
                const firstChar = escaped[0];
                const firstCode = firstChar.charCodeAt(0);
                
                // Escape leading digit (0-9: U+0030-U+0039)
                if (firstCode >= 0x30 && firstCode <= 0x39) {
                    const hex = firstCode.toString(16).toUpperCase().padStart(4, '0');
                    escaped = '\\' + hex + ' ' + escaped.slice(1);
                    hasLeadingHexEscape = true;
                }
                // Escape leading whitespace (space: U+0020, tab: U+0009, etc.)
                else if (/\s/.test(firstChar)) {
                    const hex = firstCode.toString(16).toUpperCase().padStart(4, '0');
                    escaped = '\\' + hex + ' ' + escaped.slice(1);
                    hasLeadingHexEscape = true;
                }
            }
            
            // Escape special characters
            escaped = escaped.replace(/[!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~]/g, '\\$&');
            
            // Escape internal spaces (replace spaces with \ ), but preserve space in hex escape
            if (hasLeadingHexEscape) {
                // Skip the first 6 characters (e.g., "\0030 ") when replacing spaces
                escaped = escaped.slice(0, 6) + escaped.slice(6).replace(/ /g, '\\ ');
            } else {
                escaped = escaped.replace(/ /g, '\\ ');
            }
            
            return escaped;
        }

        async function savePluginConfig(pluginId) {
            try {
                debugLog('Saving config for plugin:', pluginId);
                
                // Load schema for type detection
                let schema = {};
                try {
                    const schemaResponse = await fetch(`/api/v3/plugins/schema?plugin_id=${pluginId}`);
                    const schemaData = await schemaResponse.json();
                    if (schemaData.status === 'success' && schemaData.data.schema) {
                        schema = schemaData.data.schema;
                    }
                } catch (e) {
                    console.warn('Could not load schema for type detection:', e);
                }
                
                // Find the form in the active plugin tab
                // Alpine.js hides/shows elements with display:none, so we look for the currently visible one
                const allForms = document.querySelectorAll('form[x-on\\:submit\\.prevent]');
                debugLog('Found forms:', allForms.length);
                
                let form = null;
                for (const f of allForms) {
                    const parent = f.closest('[x-show]');
                    if (parent && parent.style.display !== 'none' && parent.offsetParent !== null) {
                        form = f;
                        debugLog('Found visible form');
                        break;
                    }
                }
                
                if (!form) {
                    throw new Error('Form not found for plugin ' + pluginId);
                }

                const formData = new FormData(form);
                const flatConfig = {};

                // First, collect all checkbox states (including unchecked ones)
                // Unchecked checkboxes don't appear in FormData, so we need to iterate form elements
                for (let i = 0; i < form.elements.length; i++) {
                    const element = form.elements[i];
                    const name = element.name;
                    
                    // Skip elements without names or submit buttons
                    if (!name || element.type === 'submit' || element.type === 'button') {
                        continue;
                    }
                    
                    // Handle checkboxes explicitly (both checked and unchecked)
                    if (element.type === 'checkbox') {
                        flatConfig[name] = element.checked;
                    }
                    // Handle radio buttons
                    else if (element.type === 'radio') {
                        if (element.checked) {
                            flatConfig[name] = element.value;
                        }
                    }
                    // Handle select elements (including multi-select)
                    else if (element.tagName === 'SELECT') {
                        if (element.multiple) {
                            // Multi-select: get all selected options
                            const selectedValues = Array.from(element.selectedOptions).map(opt => opt.value);
                            flatConfig[name] = selectedValues;
                        } else {
                            // Single select: handled by FormData, but ensure it's captured
                            if (!(name in flatConfig)) {
                                flatConfig[name] = element.value;
                            }
                        }
                    }
                    // Handle textarea
                    else if (element.tagName === 'TEXTAREA') {
                        // Textarea: handled by FormData, but ensure it's captured
                        if (!(name in flatConfig)) {
                            flatConfig[name] = element.value;
                        }
                    }
                }

                // Now process FormData for other field types
                for (const [key, value] of formData.entries()) {
                    // Skip checkboxes - we already handled them above
                    // Use querySelector to reliably find element by name (handles dot notation)
                    const escapedKey = escapeCssSelector(key);
                    const element = form.querySelector(`[name="${escapedKey}"]`);
                    if (element && element.type === 'checkbox') {
                        continue; // Already processed
                    }
                    // Skip multi-select - we already handled them above
                    if (element && element.tagName === 'SELECT' && element.multiple) {
                        continue; // Already processed
                    }
                    
                    // Get schema property type if available
                    const propSchema = getSchemaPropertyType(schema, key);
                    const propType = propSchema ? propSchema.type : null;
                    
                    // Handle based on schema type or field name patterns
                    if (propType === 'array') {
                        // Check if this is a file upload widget (JSON array in hidden input)
                        if (propSchema && propSchema['x-widget'] === 'file-upload') {
                            try {
                                // Unescape HTML entities that were escaped when setting the value
                                let unescapedValue = value;
                                if (typeof value === 'string') {
                                    // Reverse the HTML escaping: &quot; -> ", &#39; -> ', &amp; -> &
                                    unescapedValue = value
                                        .replace(/&quot;/g, '"')
                                        .replace(/&#39;/g, "'")
                                        .replace(/&lt;/g, '<')
                                        .replace(/&gt;/g, '>')
                                        .replace(/&amp;/g, '&');
                                }
                                
                                try {
                                    const jsonValue = JSON.parse(unescapedValue);
                                    if (Array.isArray(jsonValue)) {
                                        flatConfig[key] = jsonValue;
                                        debugLog(`File upload array field ${key}: parsed JSON array with ${jsonValue.length} items`);
                                    } else {
                                        // Fallback to empty array
                                        flatConfig[key] = [];
                                    }
                                } catch (e) {
                                    console.warn(`Failed to parse JSON for file upload field ${key}:`, e, 'Value:', value);
                                    // Fallback to empty array
                                    flatConfig[key] = [];
                                }
                            } catch (e) {
                                // Not valid JSON, use empty array or try comma-separated
                                if (value && value.trim()) {
                                    const arrayValue = value.split(',').map(v => v.trim()).filter(v => v);
                                    flatConfig[key] = arrayValue;
                                } else {
                                    flatConfig[key] = [];
                                }
                            }
                        } else {
                            // Regular array: convert comma-separated string to array
                            const arrayValue = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                            flatConfig[key] = arrayValue;
                        }
                    } else if (propType === 'integer') {
                        const numValue = parseInt(value, 10);
                        flatConfig[key] = isNaN(numValue) ? (propSchema && propSchema.default !== undefined ? propSchema.default : 0) : numValue;
                    } else if (propType === 'number') {
                        const numValue = parseFloat(value);
                        flatConfig[key] = isNaN(numValue) ? (propSchema && propSchema.default !== undefined ? propSchema.default : 0) : numValue;
                    } else if (propType === 'boolean') {
                        // Boolean from FormData (shouldn't happen for checkboxes, but handle it)
                        flatConfig[key] = value === 'on' || value === 'true' || value === true;
                    } else {
                        // String or other types
                        // Check if it's a number field by name pattern (fallback if no schema)
                        if (!propType && (key.includes('duration') || key.includes('interval') || 
                            key.includes('timeout') || key.includes('teams') || key.includes('fps') ||
                            key.includes('bits') || key.includes('nanoseconds') || key.includes('hz'))) {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue)) {
                                flatConfig[key] = Number.isInteger(numValue) ? parseInt(value, 10) : numValue;
                            } else {
                                flatConfig[key] = value;
                            }
                        } else {
                            flatConfig[key] = value;
                        }
                    }
                }

                // Handle unchecked checkboxes using schema (if available)
                if (schema && schema.properties) {
                    const collectBooleanFields = (props, prefix = '') => {
                        const boolFields = [];
                        for (const [key, prop] of Object.entries(props)) {
                            const fullKey = prefix ? `${prefix}.${key}` : key;
                            if (prop.type === 'boolean') {
                                boolFields.push(fullKey);
                            } else if (prop.type === 'object' && prop.properties) {
                                boolFields.push(...collectBooleanFields(prop.properties, fullKey));
                            }
                        }
                        return boolFields;
                    };
                    
                    const allBoolFields = collectBooleanFields(schema.properties);
                    allBoolFields.forEach(key => {
                        if (!(key in flatConfig)) {
                            flatConfig[key] = false;
                        }
                    });
                }

                // Convert dot notation to nested object
                const dotToNested = (obj) => {
                    const result = {};
                    for (const key in obj) {
                        const parts = key.split('.');
                        let current = result;
                        for (let i = 0; i < parts.length - 1; i++) {
                            if (!current[parts[i]]) {
                                current[parts[i]] = {};
                            }
                            current = current[parts[i]];
                        }
                        current[parts[parts.length - 1]] = obj[key];
                    }
                    return result;
                };

                const config = dotToNested(flatConfig);

                debugLog('Saving config for', pluginId, ':', config);
                debugLog('Flat config before nesting:', flatConfig);

                // Save to backend
                const response = await fetch('/api/v3/plugins/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plugin_id: pluginId, config })
                });

                let data;
                try {
                    data = await response.json();
                } catch (e) {
                    throw new Error(`Failed to parse server response: ${response.status} ${response.statusText}`);
                }

                if (!response.ok || data.status !== 'success') {
                    let errorMessage = data.message || 'Failed to save configuration';
                    if (data.validation_errors && Array.isArray(data.validation_errors)) {
                        errorMessage += '\n\nValidation errors:\n' + data.validation_errors.join('\n');
                    }
                    throw new Error(errorMessage);
                } else {
                    showNotification(`Configuration saved for ${pluginId}`, 'success');
                }

            } catch (error) {
                console.error('Error saving plugin configuration:', error);
                showNotification('Error saving plugin configuration: ' + error.message, 'error');
            }
        }
        
        // Notification helper function
        // Fix invalid number inputs before form submission
        // This prevents "invalid form control is not focusable" errors
        window.fixInvalidNumberInputs = function(form) {
            if (!form) return;
            const allInputs = form.querySelectorAll('input[type="number"]');
            allInputs.forEach(input => {
                const min = parseFloat(input.getAttribute('min'));
                const max = parseFloat(input.getAttribute('max'));
                const value = parseFloat(input.value);
                
                if (!isNaN(value)) {
                    if (!isNaN(min) && value < min) {
                        input.value = min;
                    } else if (!isNaN(max) && value > max) {
                        input.value = max;
                    }
                }
            });
        };
        
        // showNotification is provided by notification.js widget
        // This fallback is only used if the widget hasn't loaded yet
        if (typeof window.showNotification !== 'function') {
            window.showNotification = function(message, type = 'info') {
                debugLog(`[${type.toUpperCase()}]`, message);
                const notification = document.createElement('div');
                notification.className = `fixed top-4 right-4 px-6 py-3 rounded-lg shadow-lg z-50 ${
                    type === 'success' ? 'bg-green-500' : type === 'error' ? 'bg-red-500' : 'bg-blue-500'
                } text-white`;
                notification.textContent = message;
                document.body.appendChild(notification);
                setTimeout(() => { notification.style.opacity = '0'; setTimeout(() => notification.remove(), 500); }, 3000);
            };
        }

        // Section toggle function - already defined earlier, but ensure it's not overwritten
        // (duplicate definition removed - function is defined in early script block above)

        // Plugin config handler functions (idempotent initialization)
        if (!window.__pluginConfigHandlersInitialized) {
            window.__pluginConfigHandlersInitialized = true;
            
            // Initialize state on window object
            window.pluginConfigRefreshInProgress = window.pluginConfigRefreshInProgress || new Set();
            
            // Validate plugin config form and show helpful error messages
            window.validatePluginConfigForm = function(form, pluginId) {
                // Check HTML5 validation
                if (!form.checkValidity()) {
                    // Find all invalid fields
                    const invalidFields = Array.from(form.querySelectorAll(':invalid'));
                    const errors = [];
                    let firstInvalidField = null;
                    
                    invalidFields.forEach((field, index) => {
                        // Build error message
                        let fieldName = field.name || field.id || 'field';
                        // Make field name more readable (remove plugin ID prefix, convert dots/underscores)
                        fieldName = fieldName.replace(new RegExp('^' + pluginId + '-'), '')
                                            .replace(/\./g, ' → ')
                                            .replace(/_/g, ' ')
                                            .replace(/\b\w/g, l => l.toUpperCase()); // Capitalize words
                        
                        let errorMsg = field.validationMessage || 'Invalid value';
                        
                        // Get more specific error message based on validation state
                        if (field.validity.valueMissing) {
                            errorMsg = 'This field is required';
                        } else if (field.validity.rangeUnderflow) {
                            errorMsg = `Value must be at least ${field.min || 'the minimum'}`;
                        } else if (field.validity.rangeOverflow) {
                            errorMsg = `Value must be at most ${field.max || 'the maximum'}`;
                        } else if (field.validity.stepMismatch) {
                            errorMsg = `Value must be a multiple of ${field.step || 1}`;
                        } else if (field.validity.typeMismatch) {
                            errorMsg = 'Invalid format (e.g., text in number field)';
                        } else if (field.validity.patternMismatch) {
                            errorMsg = 'Value does not match required pattern';
                        } else if (field.validity.tooShort) {
                            errorMsg = `Value must be at least ${field.minLength} characters`;
                        } else if (field.validity.tooLong) {
                            errorMsg = `Value must be at most ${field.maxLength} characters`;
                        } else if (field.validity.badInput) {
                            errorMsg = 'Invalid input type';
                        }
                        
                        errors.push(`${fieldName}: ${errorMsg}`);
                        
                        // Track first invalid field for focusing
                        if (index === 0) {
                            firstInvalidField = field;
                        }
                        
                        // If field is in a collapsed section, expand it
                        const nestedContent = field.closest('.nested-content');
                        if (nestedContent && nestedContent.classList.contains('hidden')) {
                            // Find the toggle button for this section
                            const sectionId = nestedContent.id;
                            if (sectionId) {
                                // Try multiple selectors to find the toggle button
                                const toggleBtn = document.querySelector(`button[aria-controls="${sectionId}"], button[onclick*="${sectionId}"], [data-toggle-section="${sectionId}"]`) ||
                                                 nestedContent.previousElementSibling?.querySelector('button');
                                if (toggleBtn && toggleBtn.onclick) {
                                    toggleBtn.click(); // Expand the section
                                }
                            }
                        }
                    });
                    
                    // Focus and scroll to first invalid field after a brief delay
                    // (allows collapsed sections to expand first)
                    setTimeout(() => {
                        if (firstInvalidField) {
                            firstInvalidField.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            firstInvalidField.focus();
                        }
                    }, 200);
                    
                    // Show error notification with details
                    if (errors.length > 0) {
                        // Format error message nicely
                        const errorList = errors.slice(0, 5).join('\n'); // Show first 5 errors
                        const moreErrors = errors.length > 5 ? `\n... and ${errors.length - 5} more error(s)` : '';
                        const errorMessage = `Validation failed:\n${errorList}${moreErrors}`;
                        
                        if (typeof showNotification === 'function') {
                            showNotification(errorMessage, 'error');
                        } else {
                            alert(errorMessage); // Fallback if showNotification not available
                        }
                        
                        // Also log to console for debugging
                        console.error('Form validation errors:', errors);
                    }
                    
                    // Report validation failure to browser (shows native validation tooltips)
                    form.reportValidity();
                    
                    return false; // Prevent form submission
                }
                
                return true; // Validation passed
            };
            
            // Handle config save response with detailed error logging
            window.handleConfigSave = function(event, pluginId) {
                const btn = event.target.querySelector('[type=submit]');
                if (btn) btn.disabled = false;
                
                const xhr = event.detail.xhr;
                const status = xhr?.status || 0;
                
                // Check if request was successful (2xx status codes)
                if (status >= 200 && status < 300) {
                    // Try to get message from response JSON
                    let message = 'Configuration saved successfully!';
                    try {
                        if (xhr?.responseJSON?.message) {
                            message = xhr.responseJSON.message;
                        } else if (xhr?.responseText) {
                            const responseData = JSON.parse(xhr.responseText);
                            message = responseData.message || message;
                        }
                    } catch (e) {
                        // Use default message if parsing fails
                    }
                    showNotification(message, 'success');
                } else {
                    // Request failed - log detailed error information
                    console.error('Config save failed:', {
                        status: status,
                        statusText: xhr?.statusText,
                        responseText: xhr?.responseText
                    });
                    
                    // Try to parse error response
                    let errorMessage = 'Failed to save configuration';
                    try {
                        if (xhr?.responseJSON) {
                            const errorData = xhr.responseJSON;
                            errorMessage = errorData.message || errorData.details || errorMessage;
                            if (errorData.validation_errors) {
                                errorMessage += ': ' + errorData.validation_errors.join(', ');
                            }
                        } else if (xhr?.responseText) {
                            const errorData = JSON.parse(xhr.responseText);
                            errorMessage = errorData.message || errorData.details || errorMessage;
                            if (errorData.validation_errors) {
                                errorMessage += ': ' + errorData.validation_errors.join(', ');
                            }
                        }
                    } catch (e) {
                        // If parsing fails, use status text
                        errorMessage = xhr?.statusText || errorMessage;
                    }
                    
                    showNotification(errorMessage, 'error');
                }
            };
            
            // Handle toggle response
            window.handleToggleResponse = function(event, pluginId) {
                const xhr = event.detail.xhr;
                const status = xhr?.status || 0;
                
                if (status >= 200 && status < 300) {
                    // Update UI in place instead of refreshing to avoid duplication
                    const checkbox = document.getElementById(`plugin-enabled-${pluginId}`);
                    const label = checkbox?.nextElementSibling;
                    
                    if (checkbox && label) {
                        const isEnabled = checkbox.checked;
                        label.textContent = isEnabled ? 'Enabled' : 'Disabled';
                        label.className = `ml-2 text-sm ${isEnabled ? 'text-green-600' : 'text-gray-500'}`;
                    }
                    
                    // Try to get message from response
                    let message = 'Plugin status updated';
                    try {
                        if (xhr?.responseJSON?.message) {
                            message = xhr.responseJSON.message;
                        } else if (xhr?.responseText) {
                            const responseData = JSON.parse(xhr.responseText);
                            message = responseData.message || message;
                        }
                    } catch (e) {
                        // Use default message
                    }
                    showNotification(message, 'success');
                } else {
                    // Revert checkbox state on error
                    const checkbox = document.getElementById(`plugin-enabled-${pluginId}`);
                    if (checkbox) {
                        checkbox.checked = !checkbox.checked;
                    }
                    
                    // Try to get error message from response
                    let errorMessage = 'Failed to update plugin status';
                    try {
                        if (xhr?.responseJSON?.message) {
                            errorMessage = xhr.responseJSON.message;
                        } else if (xhr?.responseText) {
                            const errorData = JSON.parse(xhr.responseText);
                            errorMessage = errorData.message || errorData.details || errorMessage;
                        }
                    } catch (e) {
                        // Use default message
                    }
                    showNotification(errorMessage, 'error');
                }
            };
            
            // Handle plugin update response
            window.handlePluginUpdate = function(event, pluginId) {
                const xhr = event.detail.xhr;
                const status = xhr?.status || 0;
                
                // Check if request was successful (2xx status)
                if (status >= 200 && status < 300) {
                    // Try to parse the response to get the actual message from server
                    let message = 'Plugin updated successfully';
                    
                    if (xhr && xhr.responseText) {
                        try {
                            const data = JSON.parse(xhr.responseText);
                            // Use the server's message, ensuring it says "update" not "save"
                            message = data.message || message;
                            // Ensure message is about updating, not saving
                            if (message.toLowerCase().includes('save') && !message.toLowerCase().includes('update')) {
                                message = message.replace(/save/i, 'update');
                            }
                        } catch (e) {
                            // If parsing fails, use default message
                            console.warn('Could not parse update response:', e);
                        }
                    }
                    
                    showNotification(message, 'success');
                } else {
                    console.error('Plugin update failed:', {
                        status: status,
                        statusText: xhr?.statusText,
                        responseText: xhr?.responseText
                    });
                    
                    // Try to parse error response for better error message
                    let errorMessage = 'Failed to update plugin';
                    if (xhr?.responseText) {
                        try {
                            const errorData = JSON.parse(xhr.responseText);
                            errorMessage = errorData.message || errorMessage;
                        } catch (e) {
                            // If parsing fails, use default
                        }
                    }
                    
                    showNotification(errorMessage, 'error');
                }
            };
            
            // Refresh plugin config (with duplicate prevention)
            window.refreshPluginConfig = function(pluginId) {
                // Prevent concurrent refreshes
                if (window.pluginConfigRefreshInProgress.has(pluginId)) {
                    return;
                }
                
                const container = document.getElementById(`plugin-config-${pluginId}`);
                if (container && window.htmx) {
                    window.pluginConfigRefreshInProgress.add(pluginId);
                    
                    // Clear container first, then reload
                    container.innerHTML = '';
                    window.htmx.ajax('GET', `/v3/partials/plugin-config/${pluginId}`, {
                        target: container,
                        swap: 'innerHTML'
                    });
                    
                    // Clear flag after delay
                    setTimeout(() => {
                        window.pluginConfigRefreshInProgress.delete(pluginId);
                    }, 1000);
                }
            };
            
            // Plugin action handlers
            window.runPluginOnDemand = function(pluginId) {
                if (typeof window.openOnDemandModal === 'function') {
                    window.openOnDemandModal(pluginId);
                } else {
                    showNotification('On-demand modal not available', 'error');
                }
            };
            
            window.stopOnDemand = function() {
                if (typeof window.requestOnDemandStop === 'function') {
                    window.requestOnDemandStop({});
                } else {
                    showNotification('Stop function not available', 'error');
                }
            };
            
            window.executePluginAction = function(pluginId, actionId) {
                fetch(`/api/v3/plugins/action?plugin_id=${pluginId}&action_id=${actionId}`, {
                    method: 'POST'
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        showNotification(data.message || 'Action executed', 'success');
                    } else {
                        showNotification(data.message || 'Action failed', 'error');
                    }
                })
                .catch(err => {
                    showNotification('Failed to execute action', 'error');
                });
            };
        }

        function getAppComponent() {
            if (window.Alpine) {
                const appElement = document.querySelector('[x-data="app()"]');
                if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                    return appElement._x_dataStack[0];
                }
            }
            return null;
        }

        async function updatePlugin(pluginId) {
            try {
                showNotification(`Updating ${pluginId}...`, 'info');

                const response = await fetch('/api/v3/plugins/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plugin_id: pluginId })
                });
                const data = await response.json();

                showNotification(data.message, data.status);

                if (data.status === 'success') {
                    // Refresh the plugin list
                    const appComponent = getAppComponent();
                    if (appComponent && typeof appComponent.loadInstalledPlugins === 'function') {
                        await appComponent.loadInstalledPlugins();
                    }
                }
            } catch (error) {
                showNotification('Error updating plugin: ' + error.message, 'error');
            }
        }

        async function updateAllPlugins() {
            try {
                const plugins = Array.isArray(window.installedPlugins) ? window.installedPlugins : [];

                if (!plugins.length) {
                    showNotification('No installed plugins to update.', 'warning');
                    return;
                }

                showNotification(`Checking ${plugins.length} plugin${plugins.length === 1 ? '' : 's'} for updates...`, 'info');

                let successCount = 0;
                let failureCount = 0;

                for (const plugin of plugins) {
                    const pluginId = plugin.id;
                    const pluginName = plugin.name || pluginId;

                    try {
                        const response = await fetch('/api/v3/plugins/update', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ plugin_id: pluginId })
                        });

                        const data = await response.json();
                        const status = data.status || 'info';
                        const message = data.message || `Checked ${pluginName}`;

                        showNotification(message, status);

                        if (status === 'success') {
                            successCount += 1;
                        } else {
                            failureCount += 1;
                        }
                    } catch (error) {
                        failureCount += 1;
                        showNotification(`Error updating ${pluginName}: ${error.message}`, 'error');
                    }
                }

                const appComponent = getAppComponent();
                if (appComponent && typeof appComponent.loadInstalledPlugins === 'function') {
                    await appComponent.loadInstalledPlugins();
                }

                if (failureCount === 0) {
                    showNotification(`Finished checking ${successCount} plugin${successCount === 1 ? '' : 's'} for updates.`, 'success');
                } else {
                    showNotification(`Updated ${successCount} plugin${successCount === 1 ? '' : 's'} with ${failureCount} failure${failureCount === 1 ? '' : 's'}. Check logs for details.`, 'error');
                }
            } catch (error) {
                console.error('Bulk plugin update failed:', error);
                showNotification('Failed to update all plugins: ' + error.message, 'error');
            }
        }

        window.updateAllPlugins = updateAllPlugins;


        async function uninstallPlugin(pluginId) {
            try {
                // Get plugin info from window.installedPlugins
                const plugin = window.installedPlugins ? window.installedPlugins.find(p => p.id === pluginId) : null;
                const pluginName = plugin ? (plugin.name || pluginId) : pluginId;

                if (!confirm(`Are you sure you want to uninstall ${pluginName}?`)) {
                    return;
                }

                showNotification(`Uninstalling ${pluginName}...`, 'info');

                const response = await fetch('/api/v3/plugins/uninstall', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plugin_id: pluginId })
                });
                const data = await response.json();

                // Check if operation was queued
                if (data.status === 'success' && data.data && data.data.operation_id) {
                    // Operation was queued, poll for completion
                    const operationId = data.data.operation_id;
                    showNotification(`Uninstall queued for ${pluginName}...`, 'info');
                    await pollUninstallOperation(operationId, pluginId, pluginName);
                } else if (data.status === 'success') {
                    // Direct uninstall completed immediately
                    showNotification(data.message || `Plugin ${pluginName} uninstalled successfully`, 'success');
                    // Refresh the plugin list
                    await app.loadInstalledPlugins();
                } else {
                    // Error response
                    showNotification(data.message || 'Failed to uninstall plugin', data.status || 'error');
                }
            } catch (error) {
                showNotification('Error uninstalling plugin: ' + error.message, 'error');
            }
        }

        async function pollUninstallOperation(operationId, pluginId, pluginName, maxAttempts = 60, attempt = 0) {
            if (attempt >= maxAttempts) {
                showNotification(`Uninstall operation timed out for ${pluginName}`, 'error');
                // Refresh plugin list to see actual state
                await app.loadInstalledPlugins();
                return;
            }

            try {
                const response = await fetch(`/api/v3/plugins/operation/${operationId}`);
                const data = await response.json();
                
                if (data.status === 'success' && data.data) {
                    const operation = data.data;
                    const status = operation.status;
                    
                    if (status === 'completed') {
                        // Operation completed successfully
                        showNotification(`Plugin ${pluginName} uninstalled successfully`, 'success');
                        await app.loadInstalledPlugins();
                    } else if (status === 'failed') {
                        // Operation failed
                        const errorMsg = operation.error || operation.message || `Failed to uninstall ${pluginName}`;
                        showNotification(errorMsg, 'error');
                        // Refresh plugin list to see actual state
                        await app.loadInstalledPlugins();
                    } else if (status === 'pending' || status === 'in_progress') {
                        // Still in progress, poll again
                        await new Promise(resolve => setTimeout(resolve, 1000)); // Wait 1 second
                        await pollUninstallOperation(operationId, pluginId, pluginName, maxAttempts, attempt + 1);
                    } else {
                        // Unknown status, poll again
                        await new Promise(resolve => setTimeout(resolve, 1000));
                        await pollUninstallOperation(operationId, pluginId, pluginName, maxAttempts, attempt + 1);
                    }
                } else {
                    // Error getting operation status, try again
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    await pollUninstallOperation(operationId, pluginId, pluginName, maxAttempts, attempt + 1);
                }
            } catch (error) {
                console.error('Error polling operation status:', error);
                // On error, refresh plugin list to see actual state
                await app.loadInstalledPlugins();
            }
        }

        // Assign to window for global access
        window.uninstallPlugin = uninstallPlugin;

        async function refreshPlugin(pluginId) {
            try {
                // Switch to the plugin manager tab briefly to refresh
                const originalTab = app.activeTab;
                app.activeTab = 'plugins';

                // Wait a moment then switch back
                setTimeout(() => {
                    app.activeTab = originalTab;
                    app.showNotification(`Refreshed ${pluginId}`, 'success');
                }, 100);

            } catch (error) {
                app.showNotification('Error refreshing plugin: ' + error.message, 'error');
            }
        }

        // Format commit information for display
        function formatCommitInfo(commit, branch) {
            if (!commit && !branch) return 'Unknown';
            const shortCommit = commit ? String(commit).substring(0, 7) : '';
            const branchText = branch ? String(branch) : '';

            if (branchText && shortCommit) {
                return `${branchText} · ${shortCommit}`;
            }
            if (branchText) {
                return branchText;
            }
            if (shortCommit) {
                return shortCommit;
            }
            return 'Latest';
        }

        // Format date for display
        function formatDateInfo(dateString) {
            if (!dateString) return 'Unknown';
            
            try {
                const date = new Date(dateString);
                const now = new Date();
                const diffTime = Math.abs(now - date);
                const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
                
                if (diffDays < 1) {
                    return 'Today';
                } else if (diffDays < 2) {
                    return 'Yesterday';
                } else if (diffDays < 7) {
                    return `${diffDays} days ago`;
                } else if (diffDays < 30) {
                    const weeks = Math.floor(diffDays / 7);
                    return `${weeks} ${weeks === 1 ? 'week' : 'weeks'} ago`;
                } else {
                    // Return formatted date for older items
                    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
                }
            } catch (e) {
                return dateString;
            }
        }

        // Make functions available to Alpine.js
        window.formatCommitInfo = formatCommitInfo;
        window.formatDateInfo = formatDateInfo;

