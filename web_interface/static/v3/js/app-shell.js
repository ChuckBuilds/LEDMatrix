/* global debugLog */
/*
 * app-shell.js -- the page shell: the Alpine app(), tab loading, live
 * streams, header stats, and the handlers the plugin config tab calls.
 *
 * Deferred, and placed before alpinejs.min.js so window.app is the full
 * implementation by the time Alpine starts, and the alpine:init listener
 * (Alpine stores) is registered first.
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
 *   window.app                  the root component: activeTab, plugin tab
 *                               row, loadTabContent (htmx, with a
 *                               loadPartialDirect fallback)
 *   window.LEDVisibility        run a partial's timers only while its tab is
 *                               active and the page is visible
 *   window.LEDStreams           the one owner of the stats and display SSE
 *                               streams (window.statsSource / displaySource)
 *   window.showNotification     a queueing stand-in until the notification
 *                               widget loads
 *   Alpine stores 'onDemand' and 'plugins'
 *   Template handlers: fixInvalidNumberInputs, validatePluginConfigForm,
 *     handleConfigSave, handleToggleResponse, handlePluginUpdate,
 *     refreshPluginConfig, runPluginOnDemand, stopOnDemand,
 *     dismissPowerWarningBanner, takeScreenshot
 *   Plain functions other scripts and the Overview partial call:
 *     updateSystemStats, updateDisplayPreview, renderLedDots, drawGrid,
 *     updatePlugin (the plugin card's Update button)
 */
        function _setConnectionStatus(connected, reconnecting, paused) {
            const el = document.getElementById('connection-status');
            if (!el) return;
            if (paused) {
                // Intentionally closed while the page is hidden — not an error.
                el.innerHTML = `
                    <div class="w-2 h-2 bg-gray-200 border border-gray-300 rounded-full"></div>
                    <span class="text-gray-600" title="Live updates pause while this page is in the background">Paused</span>
                `;
            } else if (connected) {
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

        // LEDStreams attaches these to every EventSource it opens.
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

        // ===== Tab/page visibility — shared by partials =====
        // LEDVisibility.onActive(tab, start, stop[, key]) runs start() when
        // `tab` is the active tab AND the page is visible, and stop() when
        // either stops being true. start() should refresh immediately. The
        // registration is keyed (default: tab name), so a partial re-injected
        // by HTMX replaces its previous registration — the old stop() runs
        // first — instead of stacking intervals. Returns an unregister fn.
        // Tab changes are also broadcast as `ledmatrix:tab-changed` on document.
        window.LEDVisibility = (function() {
            const regs = new Map();
            let lastTab = null;

            function activeTab() {
                const app = window.getApp();
                return (app && app.activeTab) || lastTab;
            }
            function isActive(tab) {
                return !document.hidden && activeTab() === tab;
            }
            function evaluate(reg) {
                const want = isActive(reg.tab);
                if (want === reg.running) return;
                reg.running = want;
                try {
                    (want ? reg.start : reg.stop)();
                } catch (e) {
                    console.error('[LEDVisibility] ' + reg.key + (want ? ' start' : ' stop') + ' failed:', e);
                }
            }
            function onActive(tab, start, stop, key) {
                key = key || tab;
                const prev = regs.get(key);
                if (prev && prev.running) {
                    prev.running = false;
                    try { prev.stop(); } catch (e) { console.error('[LEDVisibility] ' + key + ' stop failed:', e); }
                }
                const reg = { tab: tab, start: start, stop: stop, key: key, running: false };
                regs.set(key, reg);
                evaluate(reg);
                return function() {
                    if (regs.get(key) !== reg) return;
                    regs.delete(key);
                    if (reg.running) { reg.running = false; stop(); }
                };
            }
            function refresh() {
                regs.forEach(evaluate);
                if (window.LEDStreams) window.LEDStreams.refresh();
            }

            document.addEventListener('visibilitychange', refresh);
            document.addEventListener('ledmatrix:tab-changed', function(e) {
                lastTab = (e.detail && e.detail.tab) || lastTab;
                refresh();
            });

            return { activeTab: activeTab, isActive: isActive, onActive: onActive, refresh: refresh };
        })();

        // ===== Live SSE streams — the single owner =====
        // /stream/stats feeds the header stats and connection badge on every
        // tab, so it stays open while the page is visible. /stream/display
        // pushes base64 PNG frames and is only opened while a preview is on
        // screen: the Overview live preview, or the floating preview when open.
        // Both close while the page is hidden. The server's broadcaster ends its
        // generator thread once the last subscriber leaves, so closing here
        // saves Pi CPU, not only bandwidth.
        //
        // window.statsSource / window.displaySource remain public. An
        // EventSource can't be reopened, so each reopen creates a new one;
        // listeners other code attached with addEventListener (e.g. the Tools
        // power panel) are recorded and re-attached to the replacement.
        window.LEDStreams = (function() {
            const CONFIG = {
                stats: {
                    url: '/api/v3/stream/stats',
                    prop: 'statsSource',
                    onmessage: function(data) { updateSystemStats(data); },
                    wire: function(src) {
                        src.onopen = window._statsOpenHandler;
                        src.onerror = window._statsErrorHandler;
                    }
                },
                display: {
                    url: '/api/v3/stream/display',
                    prop: 'displaySource',
                    onmessage: function(data) { updateDisplayPreview(data); },
                    wire: function(src) { src.onerror = window._displayErrorHandler; }
                }
            };
            const open = { stats: null, display: null };
            const external = { stats: [], display: [] };

            function openStream(name) {
                if (open[name]) return;
                const cfg = CONFIG[name];
                const src = new EventSource(cfg.url);
                const add = src.addEventListener.bind(src);
                const remove = src.removeEventListener.bind(src);
                src.addEventListener = function(type, listener, options) {
                    external[name].push([type, listener, options]);
                    add(type, listener, options);
                };
                src.removeEventListener = function(type, listener, options) {
                    external[name] = external[name].filter(function(l) {
                        return !(l[0] === type && l[1] === listener);
                    });
                    remove(type, listener, options);
                };
                external[name].forEach(function(l) { add(l[0], l[1], l[2]); });
                src.onmessage = function(event) {
                    let data;
                    try { data = JSON.parse(event.data); } catch { return; }
                    cfg.onmessage(data);
                };
                cfg.wire(src);
                open[name] = src;
                window[cfg.prop] = src;
            }

            // The closed instance stays on window so late `addEventListener`
            // calls are still recorded and replayed on the next open.
            function closeStream(name) {
                if (!open[name]) return;
                open[name].close();
                open[name] = null;
            }

            function previewOnScreen() {
                if (window.LEDVisibility.activeTab() === 'overview') return true;
                const panel = document.getElementById('floating-preview');
                return !!(panel && panel.style.display === 'block');
            }

            function refresh() {
                if (document.hidden) {
                    // Also covers a page first loaded in a background tab.
                    closeStream('stats');
                    closeStream('display');
                    _setConnectionStatus(false, false, true);
                    return;
                }
                if (!open.stats) {
                    _statsErrorCount = 0;
                    openStream('stats');
                }
                if (previewOnScreen()) openStream('display');
                else closeStream('display');
            }

            function reconnect() {
                closeStream('stats');
                closeStream('display');
                refresh();
            }

            return { refresh: refresh, reconnect: reconnect, isOpen: function(name) { return !!open[name]; } };
        })();

        // Header stats start immediately; the display stream opens once the
        // active tab is known (Alpine init dispatches ledmatrix:tab-changed).
        window.LEDStreams.refresh();
        document.addEventListener('DOMContentLoaded', function() { window.LEDStreams.refresh(); });

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

        // A metric the server could not read arrives as null (off a Pi there is
        // no CPU temperature); show the same '--' placeholder the page starts
        // with rather than "null°C".
        function statText(value, unit) {
            return (value == null ? '--' : value) + unit;
        }

        function updateSystemStats(data) {
            // Header: the value is the last <span> inside each stat.
            const header = [['cpu-stat', data.cpu_percent, '%'],
                            ['memory-stat', data.memory_used_percent, '%'],
                            ['temp-stat', data.cpu_temp, '°C']];
            header.forEach(([id, value, unit]) => {
                const spans = document.getElementById(id)?.querySelectorAll('span');
                if (spans && spans.length > 0) spans[spans.length - 1].textContent = statText(value, unit);
            });

            updatePowerStatus(data.power);

            // Overview tab (only present while it is loaded)
            const overview = [['cpu-usage', data.cpu_percent, '%'],
                              ['memory-usage', data.memory_used_percent, '%'],
                              ['cpu-temp', data.cpu_temp, '°C']];
            overview.forEach(([id, value, unit]) => {
                const el = document.getElementById(id);
                if (el) el.textContent = statText(value, unit);
            });

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


        // The full app(). Alpine calls it for <body x-data="app()">.
        function app() {
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
                        // Pause/resume per-tab timers and SSE (LEDVisibility)
                        document.dispatchEvent(new CustomEvent('ledmatrix:tab-changed', {
                            detail: { tab: newTab, previous: oldTab }
                        }));
                        // Trigger content load when tab changes
                        this.$nextTick(() => {
                            this.loadTabContent(newTab);
                        });
                    });

                    this.$nextTick(() => {
                        this.loadTabContent(this.activeTab);
                        if (typeof window.updateNavAriaCurrent === 'function') {
                            window.updateNavAriaCurrent(this.activeTab);
                        }
                        document.dispatchEvent(new CustomEvent('ledmatrix:tab-changed', {
                            detail: { tab: this.activeTab, previous: null }
                        }));
                    });

                    // The installed list is published by renderInstalledPlugins
                    // (plugins_manager.js) with one pluginsUpdated event.
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
                    // hx-trigger listeners. The panel is the request's source as well as
                    // its target, so htmx fires its events on the panel and the panel's
                    // hx-on::response-error handler runs. htmx resolves the promise even on
                    // an error status, so data-loaded is stamped only when no
                    // htmx:responseError fired; a failed panel reloads on the next visit.
                    // The activeTab check drops loads for a tab the user navigated away
                    // from while htmx was still loading (avoids fetching hidden panels).
                    const swap = contentEl.getAttribute('hx-swap') || 'innerHTML';
                    const load = () => {
                        if (this.activeTab !== tab || contentEl.hasAttribute('data-loaded')) {
                            contentEl.removeAttribute('data-loading');
                            return;
                        }
                        let failed = false;
                        const onError = () => { failed = true; };
                        contentEl.addEventListener('htmx:responseError', onError, { once: true });
                        return htmx.ajax('GET', url, { source: contentEl, target: contentEl, swap: swap })
                            .then(() => { if (!failed) contentEl.setAttribute('data-loaded', 'true'); })
                            .catch(() => {}) // network failure: leave unstamped so it can retry
                            .finally(() => {
                                contentEl.removeEventListener('htmx:responseError', onError);
                                contentEl.removeAttribute('data-loading');
                            });
                    };

                    if (typeof htmx !== 'undefined') {
                        load();
                        return;
                    }

                    // base.html injects htmx with a dynamic <script>, so it can arrive
                    // after Alpine starts. Poll for it; if it never arrives, fetch directly.
                    let tries = 0;
                    const timer = setInterval(() => {
                        if (typeof htmx !== 'undefined') {
                            clearInterval(timer);
                            load();
                        } else if (++tries > 100) { // ~10s
                            clearInterval(timer);
                            contentEl.removeAttribute('data-loading');
                            window.loadPartialDirect(contentEl.id, url);
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

                // Rebuilds the plugin tab row now. app-early.js recognises the
                // full implementation by the _doUpdatePluginTabs name in this
                // method's source, so keep the call spelled out.
                updatePluginTabs(retryCount = 0) {
                    this._doUpdatePluginTabs(retryCount);
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

                    // The row also holds the Plugin Manager tab, so it is always shown.
                    pluginTabsRow.style.display = 'block';

                    const existingTabs = pluginTabsNav.querySelectorAll('.plugin-tab');
                    debugLog(`[FULL] Removing ${existingTabs.length} existing plugin tabs`);
                    existingTabs.forEach(tab => tab.remove());

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
                        // break out of the attribute. escapeHtml now escapes
                        // quotes too, but setting the property directly cannot
                        // be got wrong at all, so it stays.
                        const iconEl = document.createElement('i');
                        iconEl.className = plugin.icon || 'fas fa-puzzle-piece';
                        const labelNode = document.createTextNode(plugin.name || plugin.id);
                        tabButton.replaceChildren(iconEl, labelNode);

                        pluginTabsNav.appendChild(tabButton);
                        debugLog('[FULL] Added tab for plugin:', plugin.id);
                    });

                    debugLog('[FULL] Plugin tabs update completed. Total tabs:', pluginTabsNav.querySelectorAll('.plugin-tab').length);
                },

                updatePluginTabStates() {
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
                    window.showNotification(message, type);
                }
            };

            window.app = function() {
                return fullImplementation;
            };
            
            // If Alpine started on the app-early.js stub, copy the full
            // implementation into that live component (next frame, once
            // Alpine has finished initialising it).
            if (window.Alpine) {
                requestAnimationFrame(() => {
                    if (window._appEnhanced) return;
                    window._appEnhanced = true;
                    const isAPMode = window.location.hostname === '192.168.4.1' ||
                                   window.location.hostname.startsWith('192.168.4.');
                    const defaultTab = isAPMode ? 'wifi' : 'overview';
                    const existingComponent = window.getApp();
                    if (existingComponent) {
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
        
        window.app = app;


        // ===== Display preview (Overview tab and the floating preview) =====
        
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

        // Clamps number inputs to their min/max before a form submits, so the
        // browser does not block the submit with "An invalid form control is
        // not focusable" for a field the user cannot see. Called from the
        // Display and Durations forms' onsubmit.
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
        
        // Stand-in for window.showNotification until the notification widget
        // (widgets/notification.js, in the widget bundle) loads: it queues
        // messages, and the widget shows the queue and replaces this function.
        // This script runs before every other script that notifies, so callers
        // use showNotification without checking that it exists.
        if (typeof window.showNotification !== 'function') {
            window.showNotification = function(message, type = 'info') {
                const registry = window.LEDMatrixWidgets;
                const widget = registry && typeof registry.get === 'function' ? registry.get('notification') : null;
                if (widget && typeof widget.show === 'function') {
                    return widget.show(message, typeof type === 'string' ? { type: type } : (type || {}));
                }
                if (!Array.isArray(window.__pendingNotifications)) {
                    window.__pendingNotifications = [];
                }
                window.__pendingNotifications.push([message, type]);
                debugLog(`[${String((type && type.type) || type).toUpperCase()}]`, message);
            };
        }

        // Plugin ids whose config tab is reloading (see refreshPluginConfig).
        window.pluginConfigRefreshInProgress = window.pluginConfigRefreshInProgress || new Set();

        // The parsed JSON body of an htmx request's XMLHttpRequest, or null.
        // (XMLHttpRequest has no responseJSON; that is a jQuery property.)
        function xhrJson(xhr) {
            try {
                return xhr && xhr.responseText ? JSON.parse(xhr.responseText) : null;
            } catch (e) {
                return null;
            }
        }

        /**
         * Checks a plugin config form before htmx posts it. Called from the
         * form's onsubmit in partials/plugin_config.html
         * (`return validatePluginConfigForm(this, pluginId)`).
         * On failure it expands the collapsed sections holding invalid
         * fields, focuses the first one, and shows the errors.
         * @returns {boolean} true to let the submit (and htmx) proceed,
         *   false to cancel it.
         */
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

                    showNotification(errorMessage, 'error');

                    // Also log to console for debugging
                    console.error('Form validation errors:', errors);
                }

                // Report validation failure to browser (shows native validation tooltips)
                form.reportValidity();

                return false; // Prevent form submission
            }

            return true; // Validation passed
        };

        /**
         * Reports a plugin config save. Called from the config form's
         * hx-on::after-request in partials/plugin_config.html. Re-enables
         * the submit button and shows the server's message as a success or
         * error notification. Returns nothing.
         */
        window.handleConfigSave = function(event, pluginId) {
            const btn = event.target.querySelector('[type=submit]');
            if (btn) btn.disabled = false;

            const xhr = event.detail.xhr;
            const status = xhr?.status || 0;

            // Check if request was successful (2xx status codes)
            if (status >= 200 && status < 300) {
                const data = xhrJson(xhr);
                showNotification((data && data.message) || 'Configuration saved successfully!', 'success');
            } else {
                // Request failed - log detailed error information
                console.error('Config save failed:', {
                    status: status,
                    statusText: xhr?.statusText,
                    responseText: xhr?.responseText
                });

                const errorData = xhrJson(xhr);
                let errorMessage = errorData
                    ? (errorData.message || errorData.details || 'Failed to save configuration')
                    : ((xhr && xhr.statusText) || 'Failed to save configuration');
                if (errorData && errorData.validation_errors) {
                    errorMessage += ': ' + errorData.validation_errors.join(', ');
                }
                showNotification(errorMessage, 'error');
            }
        };

        /**
         * Reports the enable/disable switch on a plugin's config tab. Called
         * from the switch's hx-on::after-request in
         * partials/plugin_config.html. On success it relabels the switch; on
         * failure it flips the checkbox back. Returns nothing.
         */
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

                const data = xhrJson(xhr);
                showNotification((data && data.message) || 'Plugin status updated', 'success');
            } else {
                // Revert checkbox state on error
                const checkbox = document.getElementById(`plugin-enabled-${pluginId}`);
                if (checkbox) {
                    checkbox.checked = !checkbox.checked;
                }

                const errorData = xhrJson(xhr);
                showNotification((errorData && (errorData.message || errorData.details)) ||
                    'Failed to update plugin status', 'error');
            }
        };

        /**
         * Reports the Update button on a plugin's config tab. Called from its
         * hx-on::after-request in partials/plugin_config.html. Shows the
         * server's message. Returns nothing.
         */
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

        /**
         * Reloads a plugin's config tab from the server. Called from the
         * Refresh button's onclick in partials/plugin_config.html. Clicks
         * within a second of a reload are ignored. Returns nothing.
         */
        window.refreshPluginConfig = function(pluginId) {
            if (window.pluginConfigRefreshInProgress.has(pluginId)) {
                return;
            }

            const container = document.getElementById(`plugin-config-${pluginId}`);
            if (container && window.htmx) {
                window.pluginConfigRefreshInProgress.add(pluginId);

                container.innerHTML = '';
                window.htmx.ajax('GET', `/v3/partials/plugin-config/${pluginId}`, {
                    target: container,
                    swap: 'innerHTML'
                });

                setTimeout(() => {
                    window.pluginConfigRefreshInProgress.delete(pluginId);
                }, 1000);
            }
        };

        // Run / Stop on-demand buttons on a plugin's config tab (onclick in
        // partials/plugin_config.html); plugins_manager.js implements both.
        window.runPluginOnDemand = function(pluginId) {
            window.openOnDemandModal(pluginId);
        };

        window.stopOnDemand = function() {
            window.requestOnDemandStop({});
        };

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
                    const appComponent = window.getApp();
                    if (appComponent && typeof appComponent.loadInstalledPlugins === 'function') {
                        await appComponent.loadInstalledPlugins();
                    }
                }
            } catch (error) {
                showNotification('Error updating plugin: ' + error.message, 'error');
            }
        }


