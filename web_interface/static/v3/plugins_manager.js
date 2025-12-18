// Define critical functions immediately so they're available before any HTML is rendered
console.log('[PLUGINS SCRIPT] Defining configurePlugin and togglePlugin at top level...');

// Expose on-demand functions early as stubs (will be replaced when IIFE runs)
window.openOnDemandModal = function(pluginId) {
    console.warn('openOnDemandModal called before initialization, waiting...');
    // Wait for the real function to be available
    let attempts = 0;
    const maxAttempts = 50; // 2.5 seconds
    const checkInterval = setInterval(() => {
        attempts++;
        if (window.__openOnDemandModalImpl) {
            clearInterval(checkInterval);
            window.__openOnDemandModalImpl(pluginId);
        } else if (attempts >= maxAttempts) {
            clearInterval(checkInterval);
            console.error('openOnDemandModal not available after waiting');
            if (typeof showNotification === 'function') {
                showNotification('On-demand modal unavailable. Please refresh the page.', 'error');
            }
        }
    }, 50);
};

window.requestOnDemandStop = function({ stopService = false } = {}) {
    console.warn('requestOnDemandStop called before initialization, waiting...');
    // Wait for the real function to be available
    let attempts = 0;
    const maxAttempts = 50; // 2.5 seconds
    const checkInterval = setInterval(() => {
        attempts++;
        if (window.__requestOnDemandStopImpl) {
            clearInterval(checkInterval);
            return window.__requestOnDemandStopImpl({ stopService });
        } else if (attempts >= maxAttempts) {
            clearInterval(checkInterval);
            console.error('requestOnDemandStop not available after waiting');
            if (typeof showNotification === 'function') {
                showNotification('On-demand stop unavailable. Please refresh the page.', 'error');
            }
            return Promise.reject(new Error('Function not available'));
        }
    }, 50);
    return Promise.resolve();
};

// Cleanup orphaned modals from previous executions to prevent duplicates when moving to body
try {
    const existingModals = document.querySelectorAll('#plugin-config-modal');
    if (existingModals.length > 0) {
        existingModals.forEach(el => {
            // Only remove modals that were moved to body (orphaned from previous loads)
            // The new modal in the current content should be inside a container, not direct body child
            if (el.parentElement === document.body) {
                console.log('[PLUGINS SCRIPT] Cleaning up orphaned plugin modal');
                el.remove();
            }
        });
    }
} catch (e) {
    console.warn('[PLUGINS SCRIPT] Error cleaning up modals:', e);
}

// Track pending render data for when DOM isn't ready yet
window.__pendingInstalledPlugins = window.__pendingInstalledPlugins || null;
window.__pendingStorePlugins = window.__pendingStorePlugins || null;
window.__pluginDomReady = window.__pluginDomReady || false;

// Set up global event delegation for plugin actions (works even before plugins are loaded)
(function setupGlobalEventDelegation() {
    // Use document-level delegation so it works for dynamically added content
    const handleGlobalPluginAction = function(event) {
        // Only handle if it's a plugin action
        const button = event.target.closest('button[data-action][data-plugin-id]') || 
                       event.target.closest('input[data-action][data-plugin-id]');
        if (!button) return;
        
        const action = button.getAttribute('data-action');
        const pluginId = button.getAttribute('data-plugin-id');
        
        // For toggle and configure, ensure functions are available
        if (action === 'toggle' || action === 'configure') {
            const funcName = action === 'toggle' ? 'togglePlugin' : 'configurePlugin';
            if (!window[funcName] || typeof window[funcName] !== 'function') {
                // Prevent default and stop propagation immediately to avoid double handling
                event.preventDefault();
                event.stopPropagation();

                console.warn(`[GLOBAL DELEGATION] ${funcName} not available yet, waiting...`);
                
                // Capture state synchronously from plugin data (source of truth)
                let targetChecked = false;
                if (action === 'toggle') {
                    const plugin = (window.installedPlugins || []).find(p => p.id === pluginId);
                    
                    let currentEnabled;
                    if (plugin) {
                        currentEnabled = Boolean(plugin.enabled);
                    } else if (button.type === 'checkbox') {
                        currentEnabled = button.checked;
                    } else {
                        currentEnabled = false;
                    }
                    
                    targetChecked = !currentEnabled; // Toggle to opposite state
                }

                // Wait for function to be available
                let attempts = 0;
                const maxAttempts = 20; // 1 second total
                const checkInterval = setInterval(() => {
                    attempts++;
                    if (window[funcName] && typeof window[funcName] === 'function') {
                        clearInterval(checkInterval);
                        // Call the function directly
                        if (action === 'toggle') {
                            window.togglePlugin(pluginId, targetChecked);
                        } else {
                            window.configurePlugin(pluginId);
                        }
                    } else if (attempts >= maxAttempts) {
                        clearInterval(checkInterval);
                        console.error(`[GLOBAL DELEGATION] ${funcName} not available after ${maxAttempts} attempts`);
                        if (typeof showNotification === 'function') {
                            showNotification(`${funcName} not loaded. Please refresh the page.`, 'error');
                        }
                    }
                }, 50);
                return; // Don't proceed with normal handling
            }
        }
        
        // Prevent default and stop propagation to avoid double handling
        event.preventDefault();
        event.stopPropagation();
        
        // If handlePluginAction exists, use it; otherwise handle directly
        if (typeof handlePluginAction === 'function') {
            handlePluginAction(event);
        } else {
            // Fallback: handle directly if functions are available
            if (action === 'toggle' && window.togglePlugin) {
                // Get the current enabled state from plugin data (source of truth)
                const plugin = (window.installedPlugins || []).find(p => p.id === pluginId);
                
                let currentEnabled;
                if (plugin) {
                    currentEnabled = Boolean(plugin.enabled);
                } else if (button.type === 'checkbox') {
                    currentEnabled = button.checked;
                } else {
                    currentEnabled = false;
                }
                
                // Toggle the state - we want the opposite of current state
                const isChecked = !currentEnabled;
                
                // Prevent default behavior to avoid double-toggling and change event
                // (Already done at start of function, but safe to repeat)
                event.preventDefault();
                event.stopPropagation();
                
                console.log('[DEBUG toggle fallback] Plugin:', pluginId, 'Current enabled (from data):', currentEnabled, 'New state:', isChecked);
                
                window.togglePlugin(pluginId, isChecked);
            } else if (action === 'configure' && window.configurePlugin) {
                event.preventDefault();
                event.stopPropagation();
                window.configurePlugin(pluginId);
            }
        }
    };
    
    // Set up delegation on document (capture phase for better reliability)
    document.addEventListener('click', handleGlobalPluginAction, true);
    document.addEventListener('change', handleGlobalPluginAction, true);
    console.log('[PLUGINS SCRIPT] Global event delegation set up');
})();

window.configurePlugin = window.configurePlugin || async function(pluginId) {
    console.log('[DEBUG] ===== configurePlugin called =====');
    console.log('[DEBUG] Plugin ID:', pluginId);
    
    // Switch to the plugin's configuration tab instead of opening a modal
    // This matches the behavior of clicking the plugin tab at the top
    function getAppComponent() {
        if (window.Alpine) {
            const appElement = document.querySelector('[x-data="app()"]');
            if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                return appElement._x_dataStack[0];
            }
        }
        return null;
    }
    
    const appComponent = getAppComponent();
    if (appComponent) {
        // Set the active tab to the plugin ID
        appComponent.activeTab = pluginId;
        console.log('[DEBUG] Switched to plugin tab:', pluginId);
        
        // Scroll to top of page to ensure the tab is visible
        window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
        console.error('Alpine.js app instance not found');
        if (typeof showNotification === 'function') {
            showNotification('Unable to switch to plugin configuration. Please refresh the page.', 'error');
        }
    }
};

window.togglePlugin = window.togglePlugin || function(pluginId, enabled) {
    console.log('[DEBUG] ===== togglePlugin called =====');
    console.log('[DEBUG] Plugin ID:', pluginId, 'Enabled:', enabled);
    const plugin = (window.installedPlugins || []).find(p => p.id === pluginId);
    const pluginName = plugin ? (plugin.name || pluginId) : pluginId;
    const action = enabled ? 'enabling' : 'disabling';
    
    // Update UI immediately for better UX
    const toggleCheckbox = document.getElementById(`toggle-${pluginId}`);
    const toggleLabel = document.getElementById(`toggle-label-${pluginId}`);
    const wrapperDiv = toggleCheckbox?.parentElement?.querySelector('.flex.items-center.gap-2');
    const toggleTrack = wrapperDiv?.querySelector('.relative.w-14');
    const toggleHandle = toggleTrack?.querySelector('.absolute');
    
    if (toggleCheckbox) toggleCheckbox.checked = enabled;
    
    // Update wrapper background and border
    if (wrapperDiv) {
        if (enabled) {
            wrapperDiv.classList.remove('bg-gray-50', 'border-gray-300');
            wrapperDiv.classList.add('bg-green-50', 'border-green-500');
        } else {
            wrapperDiv.classList.remove('bg-green-50', 'border-green-500');
            wrapperDiv.classList.add('bg-gray-50', 'border-gray-300');
        }
    }
    
    // Update toggle track
    if (toggleTrack) {
        if (enabled) {
            toggleTrack.classList.remove('bg-gray-300');
            toggleTrack.classList.add('bg-green-500');
        } else {
            toggleTrack.classList.remove('bg-green-500');
            toggleTrack.classList.add('bg-gray-300');
        }
    }
    
    // Update toggle handle
    if (toggleHandle) {
        if (enabled) {
            toggleHandle.classList.add('translate-x-full', 'border-green-500');
            toggleHandle.classList.remove('border-gray-400');
            toggleHandle.innerHTML = '<i class="fas fa-check text-green-600 text-xs"></i>';
        } else {
            toggleHandle.classList.remove('translate-x-full', 'border-green-500');
            toggleHandle.classList.add('border-gray-400');
            toggleHandle.innerHTML = '<i class="fas fa-times text-gray-400 text-xs"></i>';
        }
    }
    
    // Update label with icon and text
    if (toggleLabel) {
        if (enabled) {
            toggleLabel.className = 'text-sm font-semibold text-green-700 flex items-center gap-1.5';
            toggleLabel.innerHTML = '<i class="fas fa-toggle-on text-green-600"></i><span>Enabled</span>';
        } else {
            toggleLabel.className = 'text-sm font-semibold text-gray-600 flex items-center gap-1.5';
            toggleLabel.innerHTML = '<i class="fas fa-toggle-off text-gray-400"></i><span>Disabled</span>';
        }
    }
    
    if (typeof showNotification === 'function') {
        showNotification(`${action.charAt(0).toUpperCase() + action.slice(1)} ${pluginName}...`, 'info');
    }

    fetch('/api/v3/plugins/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId, enabled: enabled })
    })
    .then(response => response.json())
    .then(data => {
        if (typeof showNotification === 'function') {
            showNotification(data.message, data.status);
        }
        if (data.status === 'success') {
            // Update local state
            if (plugin) {
                plugin.enabled = enabled;
            }
            // Refresh the list to ensure consistency
            if (typeof loadInstalledPlugins === 'function') {
                loadInstalledPlugins();
            }
        } else {
            // Revert the toggle if API call failed
            if (plugin) {
                plugin.enabled = !enabled;
            }
            if (typeof loadInstalledPlugins === 'function') {
                loadInstalledPlugins();
            }
        }
    })
    .catch(error => {
        if (typeof showNotification === 'function') {
            showNotification('Error toggling plugin: ' + error.message, 'error');
        }
        // Revert the toggle if API call failed
        if (plugin) {
            plugin.enabled = !enabled;
        }
        if (typeof loadInstalledPlugins === 'function') {
            loadInstalledPlugins();
        }
    });
};

// Verify functions are defined immediately after assignment
console.log('[PLUGINS SCRIPT] Functions defined:', {
    configurePlugin: typeof window.configurePlugin,
    togglePlugin: typeof window.togglePlugin
});

// Double-check that functions are actually callable
if (typeof window.configurePlugin === 'function') {
    console.log('[PLUGINS SCRIPT] ✓ configurePlugin is a function and ready');
} else {
    console.error('[PLUGINS SCRIPT] ✗ configurePlugin is NOT a function!');
}

if (typeof window.togglePlugin === 'function') {
    console.log('[PLUGINS SCRIPT] ✓ togglePlugin is a function and ready');
} else {
    console.error('[PLUGINS SCRIPT] ✗ togglePlugin is NOT a function!');
}

(function() {
    'use strict';

    console.log('Plugin manager script starting...');
    
    // Local variables for this instance
let installedPlugins = [];
window.currentPluginConfig = null;
    let pluginStoreCache = null; // Cache for plugin store to speed up subsequent loads
    let cacheTimestamp = null;
    const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes in milliseconds
    let onDemandStatusInterval = null;
    let currentOnDemandPluginId = null;
    let hasLoadedOnDemandStatus = false;

    // Shared on-demand status store (mirrors Alpine store when available)
    window.__onDemandStore = window.__onDemandStore || {
        loading: true,
        state: {},
        service: {},
        error: null,
        lastUpdated: null
    };

    function ensureOnDemandStore() {
        if (window.Alpine && typeof Alpine.store === 'function') {
            if (!Alpine.store('onDemand')) {
                Alpine.store('onDemand', {
                    loading: window.__onDemandStore.loading,
                    state: window.__onDemandStore.state,
                    service: window.__onDemandStore.service,
                    error: window.__onDemandStore.error,
                    lastUpdated: window.__onDemandStore.lastUpdated
                });
            }
            const store = Alpine.store('onDemand');
            window.__onDemandStore = store;
            return store;
        }
        return window.__onDemandStore;
    }

    function markOnDemandLoading() {
        const store = ensureOnDemandStore();
        store.loading = true;
        store.error = null;
    }

    function updateOnDemandSnapshot(store) {
        if (!window.__onDemandStore) {
            window.__onDemandStore = {};
        }
        window.__onDemandStore.loading = store.loading;
        window.__onDemandStore.state = store.state;
        window.__onDemandStore.service = store.service;
        window.__onDemandStore.error = store.error;
        window.__onDemandStore.lastUpdated = store.lastUpdated;
    }

    function updateOnDemandStore(data) {
        const store = ensureOnDemandStore();
        store.loading = false;
        store.state = data?.state || {};
        store.service = data?.service || {};
        store.error = (data?.state?.status === 'error') ? (data.state.error || data.message || 'On-demand error') : null;
        store.lastUpdated = Date.now();
        updateOnDemandSnapshot(store);
        document.dispatchEvent(new CustomEvent('onDemand:updated', {
            detail: {
                state: store.state,
                service: store.service,
                error: store.error,
                lastUpdated: store.lastUpdated
            }
        }));
    }

    function setOnDemandError(message) {
        const store = ensureOnDemandStore();
        store.loading = false;
        store.state = {};
        store.service = {};
        store.error = message || 'Failed to load on-demand status';
        store.lastUpdated = Date.now();
        updateOnDemandSnapshot(store);
        document.dispatchEvent(new CustomEvent('onDemand:updated', {
            detail: {
                state: store.state,
                service: store.service,
                error: store.error,
                lastUpdated: store.lastUpdated
            }
        }));
    }

// Track initialization state
window.pluginManager = window.pluginManager || {};
window.pluginManager.initialized = false;

// Initialize when DOM is ready or when HTMX loads content
window.initPluginsPage = function() {
    if (window.pluginManager.initialized) {
        console.log('Plugin page already initialized, skipping...');
        return;
    }
    window.pluginManager.initialized = true;
    window.__pluginDomReady = true;
    
    // If we fetched data before the DOM existed, render it now
    if (window.__pendingInstalledPlugins) {
        console.log('[RENDER] Applying pending installed plugins data');
        renderInstalledPlugins(window.__pendingInstalledPlugins);
        window.__pendingInstalledPlugins = null;
    }
    if (window.__pendingStorePlugins) {
        console.log('[RENDER] Applying pending plugin store data');
        renderPluginStore(window.__pendingStorePlugins);
        window.__pendingStorePlugins = null;
    }

    initializePlugins();

    // Event listeners (remove old ones first to prevent duplicates)
    const refreshBtn = document.getElementById('refresh-plugins-btn');
    const updateAllBtn = document.getElementById('update-all-plugins-btn');
    const restartBtn = document.getElementById('restart-display-btn');
    const searchBtn = document.getElementById('search-plugins-btn');
    const closeBtn = document.getElementById('close-plugin-config');
    const closeOnDemandModalBtn = document.getElementById('close-on-demand-modal');
    const cancelOnDemandBtn = document.getElementById('cancel-on-demand');
    const onDemandForm = document.getElementById('on-demand-form');
    const onDemandModal = document.getElementById('on-demand-modal');
    
    if (refreshBtn) {
        refreshBtn.replaceWith(refreshBtn.cloneNode(true));
        document.getElementById('refresh-plugins-btn').addEventListener('click', refreshPlugins);
    }
    if (updateAllBtn) {
        updateAllBtn.replaceWith(updateAllBtn.cloneNode(true));
        document.getElementById('update-all-plugins-btn').addEventListener('click', runUpdateAllPlugins);
    }
    if (restartBtn) {
        restartBtn.replaceWith(restartBtn.cloneNode(true));
        document.getElementById('restart-display-btn').addEventListener('click', restartDisplay);
    }
    if (searchBtn) {
        searchBtn.replaceWith(searchBtn.cloneNode(true));
        document.getElementById('search-plugins-btn').addEventListener('click', searchPluginStore);
    }
    if (closeBtn) {
        closeBtn.replaceWith(closeBtn.cloneNode(true));
        document.getElementById('close-plugin-config').addEventListener('click', closePluginConfigModal);
        
        // View toggle buttons
        document.getElementById('view-toggle-form')?.addEventListener('click', () => switchPluginConfigView('form'));
        document.getElementById('view-toggle-json')?.addEventListener('click', () => switchPluginConfigView('json'));
        
        // Reset to defaults button
        document.getElementById('reset-to-defaults-btn')?.addEventListener('click', resetPluginConfigToDefaults);
        
        // JSON editor save button
        document.getElementById('save-json-config-btn')?.addEventListener('click', saveConfigFromJsonEditor);
    }
    if (closeOnDemandModalBtn) {
        closeOnDemandModalBtn.replaceWith(closeOnDemandModalBtn.cloneNode(true));
        document.getElementById('close-on-demand-modal').addEventListener('click', closeOnDemandModal);
    }
    if (cancelOnDemandBtn) {
        cancelOnDemandBtn.replaceWith(cancelOnDemandBtn.cloneNode(true));
        document.getElementById('cancel-on-demand').addEventListener('click', closeOnDemandModal);
    }
    if (onDemandForm) {
        onDemandForm.replaceWith(onDemandForm.cloneNode(true));
        document.getElementById('on-demand-form').addEventListener('submit', submitOnDemandRequest);
    }
    if (onDemandModal) {
        onDemandModal.onclick = closeOnDemandModalOnBackdrop;
    }

    loadOnDemandStatus(true);
    startOnDemandStatusPolling();
}

// Initialize when DOM is ready or when HTMX loads content
function initializePluginPageWhenReady() {
    console.log('Checking for plugin elements...');
    const installedGrid = document.getElementById('installed-plugins-grid');
    const storeGrid = document.getElementById('plugin-store-grid');
    
    // Only require installed-plugins-grid to exist (plugin-store-grid may load later)
    if (installedGrid) {
        console.log('Plugin elements found, initializing now!', {
            installedGrid: !!installedGrid,
            storeGrid: !!storeGrid
        });
        initPluginsPage();
        return true;
    } else {
        console.warn('Plugin elements not found yet. Retrying...', {
            installedGrid: !!installedGrid,
            storeGrid: !!storeGrid
        });
        return false;
    }
}

// Try multiple initialization strategies
console.log('Plugin manager script loaded, initializing...');

// Strategy 1: Immediate check (for direct page loads)
if (initializePluginPageWhenReady()) {
    console.log('Initialized immediately');
} else {
    // Strategy 2: DOMContentLoaded (for direct page loads)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            setTimeout(initializePluginPageWhenReady, 50);
        });
    }
    
    // Strategy 3: HTMX swap event (for HTMX-loaded content)
    if (typeof htmx !== 'undefined') {
        // Use htmx:afterSwap to detect when plugins content is loaded
        document.body.addEventListener('htmx:afterSwap', function(event) {
            const target = event.detail.target;
            // Check if plugins content was swapped in
            if (target.id === 'plugins-content' || 
                target.querySelector('#installed-plugins-grid') ||
                document.getElementById('installed-plugins-grid')) {
                console.log('HTMX swap detected for plugins, initializing...');
                // Reset initialization flag to allow re-initialization
                window.pluginManager.initialized = false;
                setTimeout(initializePluginPageWhenReady, 100);
            }
        });
    }
    
    // Strategy 4: Fallback timeout (for edge cases)
    setTimeout(function() {
        if (!window.pluginManager || !window.pluginManager.initialized) {
            console.log('Fallback initialization...');
            initializePluginPageWhenReady();
        }
    }, 500);
}

// Also listen for HTMX load event (fires when content is swapped)
if (typeof htmx !== 'undefined') {
    document.body.addEventListener('htmx:load', function(event) {
        // Check if plugins content was loaded (only need installed-plugins-grid)
        if (document.getElementById('installed-plugins-grid')) {
            console.log('HTMX load event detected, initializing plugins...');
            window.pluginManager.initialized = false;
            setTimeout(initializePluginPageWhenReady, 100);
        }
    });
}

// Use MutationObserver as a final fallback to detect when elements are added
if (typeof MutationObserver !== 'undefined') {
    const observer = new MutationObserver(function(mutations) {
        const installedGrid = document.getElementById('installed-plugins-grid');
        const storeGrid = document.getElementById('plugin-store-grid');
        
        // Only require installed-plugins-grid (storeGrid may load later)
        if (installedGrid && (!window.pluginManager || !window.pluginManager.initialized)) {
            console.log('MutationObserver detected plugin elements, initializing...', {
                installedGrid: !!installedGrid,
                storeGrid: !!storeGrid
            });
            setTimeout(initializePluginPageWhenReady, 100);
        }
    });
    
    // Start observing when DOM is ready
    if (document.body) {
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    } else {
        document.addEventListener('DOMContentLoaded', function() {
            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
        });
    }
}

function initializePlugins() {
    console.log('Initializing plugins...');

    // Check GitHub authentication status
    checkGitHubAuthStatus();

    // Load both installed plugins and plugin store
    loadInstalledPlugins();
    searchPluginStore(true); // Load plugin store with fresh metadata from GitHub

    // Setup search functionality
    document.getElementById('plugin-search').addEventListener('input', debounce(searchPluginStore, 300));
    document.getElementById('plugin-category').addEventListener('change', searchPluginStore);
    
    // Setup GitHub installation handlers
    setupGitHubInstallHandlers();
    
    // Setup collapsible section handlers
    setupCollapsibleSections();
    
    // Load saved repositories
    loadSavedRepositories();

    console.log('Plugins initialized');
}

function loadInstalledPlugins() {
    console.log('Loading installed plugins...');

    return fetch('/api/v3/plugins/installed')
        .then(response => {
            console.log('Installed plugins response:', response.status);
            return response.json();
        })
        .then(data => {
            console.log('Installed plugins data:', data);

            if (data.status === 'success') {
                installedPlugins = data.data.plugins || [];
                // Only update if plugin list actually changed (setter will check)
                const currentPlugins = window.installedPlugins || [];
                const currentIds = currentPlugins.map(p => p.id).sort().join(',');
                const newIds = installedPlugins.map(p => p.id).sort().join(',');
                if (currentIds !== newIds) {
                    window.installedPlugins = installedPlugins; // Also set on window for global access
                }
                console.log('Installed plugins count:', installedPlugins.length);
                
                // Debug: Log enabled status for each plugin
                installedPlugins.forEach(plugin => {
                    console.log(`[DEBUG] Plugin ${plugin.id}: enabled=${plugin.enabled} (type: ${typeof plugin.enabled})`);
                });
                
                renderInstalledPlugins(installedPlugins);

                // Update count
                const countEl = document.getElementById('installed-count');
                if (countEl) {
                    countEl.textContent = installedPlugins.length + ' installed';
                }
                return installedPlugins;
            } else {
                const errorMsg = 'Failed to load installed plugins: ' + data.message;
                showError(errorMsg);
                throw new Error(errorMsg);
            }
        })
        .catch(error => {
            console.error('Error loading installed plugins:', error);
            let errorMsg = 'Error loading plugins: ' + error.message;
            if (error.message && error.message.includes('Failed to Fetch')) {
                errorMsg += ' - Please try refreshing your browser.';
            }
            showError(errorMsg);
            throw error;
        });
}

// Expose loadInstalledPlugins on window.pluginManager for Alpine.js integration
window.pluginManager.loadInstalledPlugins = loadInstalledPlugins;

function renderInstalledPlugins(plugins) {
    const container = document.getElementById('installed-plugins-grid');
    if (!container) {
        console.warn('[RENDER] installed-plugins-grid not yet available, deferring render until plugin tab loads');
        window.__pendingInstalledPlugins = plugins;
        return;
    }
    
    // Update global installedPlugins for navigation tabs
    // Only update if plugin list actually changed
    const currentPlugins = window.installedPlugins || [];
    const currentIds = currentPlugins.map(p => p.id).sort().join(',');
    const newIds = plugins.map(p => p.id).sort().join(',');
    
    if (currentIds !== newIds) {
        window.installedPlugins = plugins;
        console.log('Set window.installedPlugins to:', plugins.length, 'plugins');
        
        // Trigger the main app to update plugin tabs
        if (window.Alpine && document.querySelector('[x-data="app()"]')) {
            const appElement = document.querySelector('[x-data="app()"]');
            if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                appElement._x_dataStack[0].installedPlugins = plugins;
                appElement._x_dataStack[0].updatePluginTabs();
                console.log('Triggered Alpine.js to update plugin tabs');
            }
        }
    } else {
        // Plugin list hasn't changed, skip update
        console.log('Plugin list unchanged, skipping tab update');
    }

    if (plugins.length === 0) {
        container.innerHTML = `
            <div class="col-span-full empty-state">
                <div class="empty-state-icon">
                    <i class="fas fa-plug"></i>
                </div>
                <p class="text-lg font-medium text-gray-700 mb-1">No plugins installed</p>
                <p class="text-sm text-gray-500">Install plugins from the store to get started</p>
            </div>
        `;
        return;
    }

    // Helper function to escape attributes for use in HTML
    const escapeAttr = (text) => {
        return (text || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    };
    
    // Helper function to escape for JavaScript strings (use JSON.stringify for proper escaping)
    // JSON.stringify returns a quoted string, so we can use it directly in JavaScript
    const escapeJs = (text) => {
        return JSON.stringify(text || '');
    };

    container.innerHTML = plugins.map(plugin => {
        // Debug: Log enabled status during rendering
        const enabledValue = plugin.enabled;
        const enabledType = typeof enabledValue;
        const enabledBool = Boolean(enabledValue);
        console.log(`[DEBUG RENDER] Plugin ${plugin.id}: enabled=${enabledValue} (type: ${enabledType}, bool: ${enabledBool})`);
        
        // Escape plugin ID for use in HTML attributes and JavaScript
        const escapedPluginId = escapeAttr(plugin.id);
        
        return `
        <div class="plugin-card">
            <div class="flex items-start justify-between mb-4">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center flex-wrap gap-2 mb-2">
                        <h4 class="font-semibold text-gray-900 text-base">${escapeHtml(plugin.name || plugin.id)}</h4>
                        ${plugin.verified ? '<span class="badge badge-success"><i class="fas fa-check-circle mr-1"></i>Verified</span>' : ''}
                    </div>
                    <div class="text-sm text-gray-600 space-y-1.5 mb-3">
                        <p class="flex items-center"><i class="fas fa-user mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.author || 'Unknown')}</p>
                        <p class="flex items-center"><i class="fas fa-code-branch mr-2 text-gray-400 w-4"></i>${formatCommit(plugin.last_commit, plugin.branch)}</p>
                        <p class="flex items-center"><i class="fas fa-calendar mr-2 text-gray-400 w-4"></i>${formatDate(plugin.last_updated)}</p>
                        <p class="flex items-center"><i class="fas fa-folder mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.category || 'General')}</p>
                        ${plugin.stars ? `<p class="flex items-center"><i class="fas fa-star mr-2 text-gray-400 w-4"></i>${plugin.stars} stars</p>` : ''}
                    </div>
                    <p class="text-sm text-gray-700 leading-relaxed">${escapeHtml(plugin.description || 'No description available')}</p>
                </div>
                <!-- Toggle Switch in Top Right -->
                <div class="flex-shrink-0 ml-4">
                    <label class="relative inline-flex items-center cursor-pointer group">
                        <input type="checkbox" 
                               class="sr-only peer" 
                               id="toggle-${escapedPluginId}"
                               ${enabledBool ? 'checked' : ''}
                               data-plugin-id="${escapedPluginId}"
                               data-action="toggle">
                        <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg border-2 transition-all duration-200 ${enabledBool ? 'bg-green-50 border-green-500' : 'bg-gray-50 border-gray-300'} hover:shadow-md group-hover:scale-105">
                            <!-- Toggle Switch -->
                            <div class="relative w-14 h-7 ${enabledBool ? 'bg-green-500' : 'bg-gray-300'} peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:bg-green-500 transition-colors duration-200 ease-in-out shadow-inner">
                                <div class="absolute top-[3px] left-[3px] bg-white ${enabledBool ? 'translate-x-full' : ''} border-2 ${enabledBool ? 'border-green-500' : 'border-gray-400'} rounded-full h-5 w-5 transition-all duration-200 ease-in-out shadow-sm flex items-center justify-center">
                                    ${enabledBool ? '<i class="fas fa-check text-green-600 text-xs"></i>' : '<i class="fas fa-times text-gray-400 text-xs"></i>'}
                                </div>
                            </div>
                            <!-- Label with Icon -->
                            <span class="text-sm font-semibold ${enabledBool ? 'text-green-700' : 'text-gray-600'} flex items-center gap-1.5" id="toggle-label-${escapedPluginId}">
                                ${enabledBool ? '<i class="fas fa-toggle-on text-green-600"></i>' : '<i class="fas fa-toggle-off text-gray-400"></i>'}
                                <span>${enabledBool ? 'Enabled' : 'Disabled'}</span>
                            </span>
                        </div>
                    </label>
                </div>
            </div>

            <!-- Plugin Tags -->
            ${plugin.tags && plugin.tags.length > 0 ? `
                <div class="flex flex-wrap gap-1.5 mb-4">
                    ${plugin.tags.map(tag => `<span class="badge badge-info">${escapeHtml(tag)}</span>`).join('')}
                </div>
            ` : ''}

            <!-- Plugin Actions -->
            <div class="flex flex-wrap gap-2 mt-4 pt-4 border-t border-gray-200">
                <button class="btn bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold"
                        data-plugin-id="${escapedPluginId}"
                        data-action="configure">
                    <i class="fas fa-cog mr-2"></i>Configure
                </button>
                <button class="btn bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold"
                        data-plugin-id="${escapedPluginId}"
                        data-action="update">
                    <i class="fas fa-sync mr-2"></i>Update
                </button>
                <button class="btn bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold"
                        data-plugin-id="${escapedPluginId}"
                        data-action="uninstall">
                    <i class="fas fa-trash mr-2"></i>Uninstall
                </button>
            </div>
        </div>
        `;
    }).join('');
    
    // Set up event delegation for plugin action buttons (fallback if onclick doesn't work)
    // Set up immediately and also retry to ensure it's attached
    const setupEventDelegation = () => {
        const container = document.getElementById('installed-plugins-grid');
        if (container) {
            // Remove old listeners if any (use a named function reference)
            const oldHandler = container._pluginActionHandler;
            if (oldHandler) {
                container.removeEventListener('click', oldHandler);
                container.removeEventListener('change', oldHandler);
            }
            // Store reference for future removal
            container._pluginActionHandler = handlePluginAction;
            // Add new listeners for both click and change events
            container.addEventListener('click', handlePluginAction, true); // Use capture phase
            container.addEventListener('change', handlePluginAction, true); // Use capture phase
            console.log('[RENDER] Event delegation set up for installed-plugins-grid');
        } else {
            console.warn('[RENDER] installed-plugins-grid not found for event delegation');
        }
    };
    
    // Set up immediately
    setupEventDelegation();
    
    // Also retry after a short delay to ensure it's attached even if container wasn't ready
    setTimeout(setupEventDelegation, 100);
}

function handlePluginAction(event) {
    // Check for both button and input (for toggle)
    const button = event.target.closest('button[data-action]') || event.target.closest('input[data-action]');
    if (!button) return;
    
    const action = button.getAttribute('data-action');
    const pluginId = button.getAttribute('data-plugin-id');
    
    if (!pluginId) return;
    
    event.preventDefault();
    event.stopPropagation();
    
    console.log('[EVENT DELEGATION] Plugin action:', action, 'Plugin ID:', pluginId);
    
    // Helper function to wait for a function to be available
    const waitForFunction = (funcName, maxAttempts = 10, delay = 50) => {
        return new Promise((resolve, reject) => {
            let attempts = 0;
            const check = () => {
                attempts++;
                if (window[funcName] && typeof window[funcName] === 'function') {
                    resolve(window[funcName]);
                } else if (attempts >= maxAttempts) {
                    reject(new Error(`${funcName} not available after ${maxAttempts} attempts`));
                } else {
                    setTimeout(check, delay);
                }
            };
            check();
        });
    };
    
    switch(action) {
        case 'toggle':
            // Get the current enabled state from plugin data (source of truth)
            // rather than from the checkbox DOM which might be out of sync
            const plugin = (window.installedPlugins || []).find(p => p.id === pluginId);
            
            // Special handling: If plugin data isn't found or is stale, fallback to DOM but be careful
            // If the user clicked the checkbox, the 'checked' property has *already* toggled in the DOM
            // (even though we preventDefault later, sometimes it's too late for the property read)
            // However, we used preventDefault() in the global handler, so the checkbox state *should* be reliable if we didn't touch it.
            
            // BUT: The issue is that 'currentEnabled' calculation might be wrong if window.installedPlugins is outdated.
            // If the user toggles ON, enabled becomes true. If they click again, we want enabled=false.
            
            // Let's try a simpler approach: Use the checkbox state as the source of truth for the *desired* state
            // Since we preventDefault(), the checkbox state reflects the *old* state (before the click)
            // wait... if we preventDefault() on 'click', the checkbox does NOT change visually or internally.
            // So button.checked is the OLD state.
            // We want the NEW state to be !button.checked.
            
            let currentEnabled;
            
            if (plugin) {
                currentEnabled = Boolean(plugin.enabled);
            } else if (button.type === 'checkbox') {
                currentEnabled = button.checked;
            } else {
                currentEnabled = false;
            }

            // Toggle the state - we want the opposite of current state
            const isChecked = !currentEnabled;
            
            console.log('[DEBUG toggle] Plugin:', pluginId, 'Current enabled (from data):', currentEnabled, 'New state:', isChecked, 'Event type:', event.type);

            waitForFunction('togglePlugin', 10, 50)
                .then(toggleFunc => {
                    toggleFunc(pluginId, isChecked);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    if (typeof showNotification === 'function') {
                        showNotification('Toggle function not loaded. Please refresh the page.', 'error');
                    } else {
                        alert('Toggle function not loaded. Please refresh the page.');
                    }
                });
            break;
        case 'configure':
            waitForFunction('configurePlugin', 10, 50)
                .then(configureFunc => {
                    configureFunc(pluginId);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    if (typeof showNotification === 'function') {
                        showNotification('Configure function not loaded. Please refresh the page.', 'error');
                    } else {
                        alert('Configure function not loaded. Please refresh the page.');
                    }
                });
            break;
        case 'update':
            waitForFunction('updatePlugin', 10, 50)
                .then(updateFunc => {
                    updateFunc(pluginId);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    if (typeof showNotification === 'function') {
                        showNotification('Update function not loaded. Please refresh the page.', 'error');
                    } else {
                        alert('Update function not loaded. Please refresh the page.');
                    }
                });
            break;
        case 'uninstall':
            waitForFunction('uninstallPlugin', 10, 50)
                .then(uninstallFunc => {
                    uninstallFunc(pluginId);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    if (typeof showNotification === 'function') {
                        showNotification('Uninstall function not loaded. Please refresh the page.', 'error');
                    } else {
                        alert('Uninstall function not loaded. Please refresh the page.');
                    }
                });
            break;
    }
}

function findInstalledPlugin(pluginId) {
    const plugins = window.installedPlugins || installedPlugins || [];
    if (!plugins || plugins.length === 0) {
        return undefined;
    }
    return plugins.find(plugin => plugin.id === pluginId);
}

function resolvePluginDisplayName(pluginId) {
    const plugin = findInstalledPlugin(pluginId);
    if (!plugin) {
        return pluginId;
    }
    return plugin.name || pluginId;
}

function loadOnDemandStatus(fromRefreshButton = false) {
    if (!hasLoadedOnDemandStatus || fromRefreshButton) {
        markOnDemandLoading();
    }

    return fetch('/api/v3/display/on-demand/status')
        .then(response => response.json())
        .then(result => {
            if (result.status === 'success') {
                updateOnDemandStore(result.data);
                hasLoadedOnDemandStatus = true;
                if (fromRefreshButton && typeof showNotification === 'function') {
                    showNotification('On-demand status refreshed', 'success');
                }
            } else {
                const message = result.message || 'Failed to load on-demand status';
                setOnDemandError(message);
                if (typeof showNotification === 'function') {
                    showNotification(message, 'error');
                }
            }
        })
        .catch(error => {
            console.error('Error fetching on-demand status:', error);
            setOnDemandError(error?.message || 'Error fetching on-demand status');
            if (typeof showNotification === 'function') {
                showNotification('Error fetching on-demand status: ' + error.message, 'error');
            }
        });
}

function startOnDemandStatusPolling() {
    if (onDemandStatusInterval) {
        clearInterval(onDemandStatusInterval);
    }
    onDemandStatusInterval = setInterval(() => loadOnDemandStatus(false), 15000);
}

window.loadOnDemandStatus = loadOnDemandStatus;

function runUpdateAllPlugins() {
    const button = document.getElementById('update-all-plugins-btn');

    if (!button) {
        if (typeof showNotification === 'function') {
            showNotification('Unable to locate bulk update controls. Refresh the Plugin Manager tab.', 'error');
        } else {
            console.error('update-all-plugins-btn element not found');
        }
        return;
    }

    if (button.dataset.running === 'true') {
        return;
    }

    if (typeof window.updateAllPlugins !== 'function') {
        if (typeof showNotification === 'function') {
            showNotification('Bulk update handler unavailable. Refresh the Plugin Manager tab.', 'error');
        } else {
            console.error('window.updateAllPlugins is not defined');
        }
        return;
    }

    const originalContent = button.innerHTML;
    button.dataset.running = 'true';
    button.disabled = true;
    button.classList.add('opacity-60', 'cursor-wait');
    button.innerHTML = '<i class="fas fa-sync fa-spin mr-2"></i>Updating...';

    Promise.resolve(window.updateAllPlugins())
        .catch(error => {
            console.error('Error updating all plugins:', error);
            if (typeof showNotification === 'function') {
                showNotification('Error updating all plugins: ' + error.message, 'error');
            }
        })
        .finally(() => {
            button.innerHTML = originalContent;
            button.disabled = false;
            button.classList.remove('opacity-60', 'cursor-wait');
            button.dataset.running = 'false';
        });
}

// Store the real implementation and replace the stub
window.__openOnDemandModalImpl = function(pluginId) {
    const plugin = findInstalledPlugin(pluginId);
    if (!plugin) {
        if (typeof showNotification === 'function') {
            showNotification(`Plugin ${pluginId} not found`, 'error');
        }
        return;
    }

    if (!plugin.enabled) {
        if (typeof showNotification === 'function') {
            showNotification('Enable the plugin before running it on-demand.', 'error');
        }
        return;
    }

    currentOnDemandPluginId = pluginId;

    const modal = document.getElementById('on-demand-modal');
    const modeSelect = document.getElementById('on-demand-mode');
    const modeHint = document.getElementById('on-demand-mode-hint');
    const durationInput = document.getElementById('on-demand-duration');
    const pinnedCheckbox = document.getElementById('on-demand-pinned');
    const startServiceCheckbox = document.getElementById('on-demand-start-service');
    const modalTitle = document.getElementById('on-demand-modal-title');

    if (!modal || !modeSelect || !modeHint || !durationInput || !pinnedCheckbox || !startServiceCheckbox || !modalTitle) {
        console.error('On-demand modal elements not found');
        return;
    }

    modalTitle.textContent = `Run ${resolvePluginDisplayName(pluginId)} On-Demand`;
    modeSelect.innerHTML = '';

    const displayModes = Array.isArray(plugin.display_modes) && plugin.display_modes.length > 0
        ? plugin.display_modes
        : [pluginId];

    displayModes.forEach(mode => {
        const option = document.createElement('option');
        option.value = mode;
        option.textContent = mode;
        modeSelect.appendChild(option);
    });

    if (displayModes.length > 1) {
        modeHint.textContent = 'Select the display mode to show on the matrix.';
    } else {
        modeHint.textContent = 'This plugin exposes a single display mode.';
    }

    durationInput.value = '';
    pinnedCheckbox.checked = false;
    startServiceCheckbox.checked = true;

    // Check service status and show warning if needed
    fetch('/api/v3/display/on-demand/status')
        .then(response => response.json())
        .then(data => {
            const serviceWarning = document.getElementById('on-demand-service-warning');
            const serviceActive = data?.data?.service?.active || false;
            
            if (serviceWarning) {
                if (!serviceActive) {
                    serviceWarning.classList.remove('hidden');
                    // Auto-check the start service checkbox
                    startServiceCheckbox.checked = true;
                } else {
                    serviceWarning.classList.add('hidden');
                }
            }
        })
        .catch(error => {
            console.error('Error checking service status:', error);
        });

    modal.style.display = 'flex';
};

// Replace the stub with the real implementation
window.openOnDemandModal = window.__openOnDemandModalImpl;

function closeOnDemandModal() {
    const modal = document.getElementById('on-demand-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    currentOnDemandPluginId = null;
}

function submitOnDemandRequest(event) {
    event.preventDefault();
    if (!currentOnDemandPluginId) {
        if (typeof showNotification === 'function') {
            showNotification('Select a plugin before starting on-demand mode.', 'error');
        }
        return;
    }

    const form = document.getElementById('on-demand-form');
    if (!form) {
        return;
    }

    const formData = new FormData(form);
    const mode = formData.get('mode');
    const pinned = formData.get('pinned') === 'on';
    const startService = formData.get('start_service') === 'on';
    const durationValue = formData.get('duration');

    const payload = {
        plugin_id: currentOnDemandPluginId,
        mode,
        pinned,
        start_service: startService
    };

    if (durationValue !== null && durationValue !== '') {
        const parsedDuration = parseInt(durationValue, 10);
        if (!Number.isNaN(parsedDuration) && parsedDuration >= 0) {
            payload.duration = parsedDuration;
        }
    }

    markOnDemandLoading();

    fetch('/api/v3/display/on-demand/start', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    })
        .then(response => response.json())
        .then(result => {
            if (result.status === 'success') {
                if (typeof showNotification === 'function') {
                    const pluginName = resolvePluginDisplayName(currentOnDemandPluginId);
                    showNotification(`Requested on-demand mode for ${pluginName}`, 'success');
                }
                closeOnDemandModal();
                setTimeout(() => loadOnDemandStatus(true), 700);
            } else {
                if (typeof showNotification === 'function') {
                    showNotification(result.message || 'Failed to start on-demand mode', 'error');
                }
            }
        })
        .catch(error => {
            console.error('Error starting on-demand mode:', error);
            if (typeof showNotification === 'function') {
                showNotification('Error starting on-demand mode: ' + error.message, 'error');
            }
        });
}

function requestOnDemandStop({ stopService = false } = {}) {
    markOnDemandLoading();
    return fetch('/api/v3/display/on-demand/stop', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            stop_service: stopService
        })
    })
        .then(response => response.json())
        .then(result => {
            if (result.status === 'success') {
                if (typeof showNotification === 'function') {
                    const message = stopService
                        ? 'On-demand mode stop requested and display service will be stopped.'
                        : 'On-demand mode stop requested';
                    showNotification(message, 'success');
                }
                setTimeout(() => loadOnDemandStatus(true), 700);
            } else {
                if (typeof showNotification === 'function') {
                    showNotification(result.message || 'Failed to stop on-demand mode', 'error');
                }
            }
        })
        .catch(error => {
            console.error('Error stopping on-demand mode:', error);
            if (typeof showNotification === 'function') {
                showNotification('Error stopping on-demand mode: ' + error.message, 'error');
            }
        });
}

function stopOnDemand(event) {
    const stopService = event && event.shiftKey;
    requestOnDemandStop({ stopService });
}

// Store the real implementation and replace the stub
window.__requestOnDemandStopImpl = requestOnDemandStop;
window.requestOnDemandStop = requestOnDemandStop;

function closeOnDemandModalOnBackdrop(event) {
    if (event.target === event.currentTarget) {
        closeOnDemandModal();
    }
}

// configurePlugin is already defined at the top of the script - no need to redefine

window.showPluginConfigModal = function(pluginId, config) {
    const modal = document.getElementById('plugin-config-modal');
    const title = document.getElementById('plugin-config-title');
    const content = document.getElementById('plugin-config-content');
    
    if (!modal) {
        console.error('[DEBUG] Plugin config modal element not found');
        if (typeof showError === 'function') {
            showError('Plugin configuration modal not found. Please refresh the page.');
        } else if (typeof showNotification === 'function') {
            showNotification('Plugin configuration modal not found. Please refresh the page.', 'error');
        }
        return;
    }

    console.log('[DEBUG] ===== Opening plugin config modal =====');
    console.log('[DEBUG] Plugin ID:', pluginId);
    console.log('[DEBUG] Config:', config);
    
    // Check if modal elements exist (already checked above, but double-check for safety)
    if (!title) {
        console.error('[DEBUG] Plugin config title element not found');
        if (typeof showError === 'function') {
            showError('Plugin configuration title element not found.');
        } else if (typeof showNotification === 'function') {
            showNotification('Plugin configuration title element not found.', 'error');
        }
        return;
    }
    
    if (!content) {
        console.error('[DEBUG] Plugin config content element not found');
        if (typeof showError === 'function') {
            showError('Plugin configuration content element not found.');
        } else if (typeof showNotification === 'function') {
            showNotification('Plugin configuration content element not found.', 'error');
        }
        return;
    }
    
    // Initialize state
    currentPluginConfigState.pluginId = pluginId;
    currentPluginConfigState.config = config || {};
    currentPluginConfigState.jsonEditor = null;
    
    // Reset view to form
    switchPluginConfigView('form');
    
    // Hide validation errors
    displayValidationErrors([]);
    
    title.textContent = `Configure ${pluginId}`;
    
    // Show loading state while form is generated
    content.innerHTML = '<div class="flex items-center justify-center py-8"><i class="fas fa-spinner fa-spin text-2xl text-blue-600"></i></div>';
    
    // Move modal to body to avoid z-index/overflow issues
    if (modal.parentElement !== document.body) {
        document.body.appendChild(modal);
    }

    // Remove any inline display:none that might be in the HTML FIRST
    // This is critical because the HTML template has style="display: none;" inline
    // We need to remove it before setting new styles
    let currentStyle = modal.getAttribute('style') || '';
    if (currentStyle.includes('display: none') || currentStyle.includes('display:none')) {
        currentStyle = currentStyle.replace(/display:\s*none[;]?/gi, '').trim();
        // Clean up any double semicolons or trailing semicolons
        currentStyle = currentStyle.replace(/;;+/g, ';').replace(/^;|;$/g, '');
        if (currentStyle) {
            modal.setAttribute('style', currentStyle);
        } else {
            modal.removeAttribute('style');
        }
    }

    // Show modal immediately - use important to override any other styles
    // Also ensure visibility, opacity, and z-index are set correctly
    modal.style.setProperty('display', 'flex', 'important');
    modal.style.setProperty('visibility', 'visible', 'important');
    modal.style.setProperty('opacity', '1', 'important');
    modal.style.setProperty('z-index', '9999', 'important');
    modal.style.setProperty('position', 'fixed', 'important');
    
    // Ensure modal content is also visible
    const modalContent = modal.querySelector('.modal-content');
    if (modalContent) {
        modalContent.style.setProperty('display', 'block', 'important');
        modalContent.style.setProperty('visibility', 'visible', 'important');
        modalContent.style.setProperty('opacity', '1', 'important');
    }
    
    console.log('[DEBUG] Modal display set to flex');
    console.log('[DEBUG] Modal computed style:', window.getComputedStyle(modal).display);
    console.log('[DEBUG] Modal z-index:', window.getComputedStyle(modal).zIndex);
    console.log('[DEBUG] Modal visibility:', window.getComputedStyle(modal).visibility);
    console.log('[DEBUG] Modal opacity:', window.getComputedStyle(modal).opacity);
    console.log('[DEBUG] Modal in DOM:', document.body.contains(modal));
    console.log('[DEBUG] Modal parent:', modal.parentElement?.tagName);
    console.log('[DEBUG] Modal rect:', modal.getBoundingClientRect());
    
    // Load schema for validation
    fetch(`/api/v3/plugins/schema?plugin_id=${pluginId}`)
        .then(r => r.json())
        .then(schemaData => {
            if (schemaData.status === 'success' && schemaData.data?.schema) {
                currentPluginConfigState.schema = schemaData.data.schema;
            }
        })
        .catch(err => console.warn('Could not load schema:', err));
    
    // Generate form asynchronously
    generatePluginConfigForm(pluginId, config)
        .then(formHtml => {
            console.log('[DEBUG] Form generated, setting content. HTML length:', formHtml.length);
            content.innerHTML = formHtml;
            
            // Attach form submit handler after form is inserted
            const form = document.getElementById('plugin-config-form');
            if (form) {
                form.addEventListener('submit', handlePluginConfigSubmit);
                console.log('Form submit handler attached');
            }
            
        })
        .catch(error => {
            console.error('Error generating config form:', error);
            content.innerHTML = '<p class="text-red-600">Error loading configuration form</p>';
        });
}

// Helper function to get the full property object from schema
function getSchemaProperty(schema, path) {
    if (!schema || !schema.properties) return null;
    
    const parts = path.split('.');
    let current = schema.properties;
    
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (current && current[part]) {
            if (i === parts.length - 1) {
                // Last part - return the property
                return current[part];
            } else if (current[part].properties) {
                // Navigate into nested object
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

// Helper function to find property type in nested schema using dot notation
function getSchemaPropertyType(schema, path) {
    const prop = getSchemaProperty(schema, path);
    return prop; // Return the full property object (was returning just type, but callers expect object)
}

// Helper function to convert dot notation to nested object
function dotToNested(obj) {
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
}

// Helper function to collect all boolean fields from schema (including nested)
function collectBooleanFields(schema, prefix = '') {
    const boolFields = [];
    
    if (!schema || !schema.properties) return boolFields;
    
    Object.entries(schema.properties).forEach(([key, prop]) => {
        const fullKey = prefix ? `${prefix}.${key}` : key;
        
        if (prop.type === 'boolean') {
            boolFields.push(fullKey);
        } else if (prop.type === 'object' && prop.properties) {
            boolFields.push(...collectBooleanFields(prop, fullKey));
        }
    });
    
    return boolFields;
}

function handlePluginConfigSubmit(e) {
    e.preventDefault();
    console.log('Form submitted');
    
    if (!currentPluginConfig) {
        showNotification('Plugin configuration not loaded', 'error');
        return;
    }
    
    const pluginId = currentPluginConfig.pluginId;
    const schema = currentPluginConfig.schema;
    const form = e.target;
    
    // Fix invalid hidden fields before submission
    // This prevents "invalid form control is not focusable" errors
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
    
    const formData = new FormData(form);
    const flatConfig = {};
    
    console.log('Schema loaded:', schema ? 'Yes' : 'No');
    
    // Process form data with type conversion (using dot notation for nested fields)
    for (const [key, value] of formData.entries()) {
        // Check if this is a patternProperties hidden input (contains JSON data)
        if (key.endsWith('_data') || key.includes('_data')) {
            try {
                const baseKey = key.replace(/_data$/, '');
                const jsonValue = JSON.parse(value);
                if (typeof jsonValue === 'object' && !Array.isArray(jsonValue)) {
                    flatConfig[baseKey] = jsonValue;
                    console.log(`PatternProperties field ${baseKey}: parsed JSON object`, jsonValue);
                    continue; // Skip normal processing for patternProperties
                }
            } catch (e) {
                // Not valid JSON, continue with normal processing
            }
        }
        
        // Skip key_value pair inputs (they're handled by the hidden _data input)
        if (key.includes('[key_') || key.includes('[value_')) {
            continue;
        }
        
        // Try to get schema property - handle both dot notation and underscore notation
        let propSchema = getSchemaPropertyType(schema, key);
        let actualKey = key;
        let actualValue = value;
        
        // If not found with dots, try converting underscores to dots (for nested fields)
        if (!propSchema && key.includes('_')) {
            const dotKey = key.replace(/_/g, '.');
            propSchema = getSchemaPropertyType(schema, dotKey);
            if (propSchema) {
                // Use the dot notation key for consistency
                actualKey = dotKey;
                actualValue = value;
            }
        }
        
        if (propSchema) {
            const propType = propSchema.type;
            
            if (propType === 'array') {
                // Check if this is a file upload widget (JSON array)
                if (propSchema['x-widget'] === 'file-upload') {
                    // Try to parse as JSON first (for file uploads)
                    try {
                        // Handle HTML entity encoding (from hidden input)
                        let decodedValue = actualValue;
                        if (typeof actualValue === 'string') {
                            // Decode HTML entities if present
                            const tempDiv = document.createElement('div');
                            tempDiv.innerHTML = actualValue;
                            decodedValue = tempDiv.textContent || tempDiv.innerText || actualValue;
                        }
                        
                        const jsonValue = JSON.parse(decodedValue);
                        if (Array.isArray(jsonValue)) {
                            flatConfig[actualKey] = jsonValue;
                            console.log(`File upload array field ${actualKey}: parsed JSON array with ${jsonValue.length} items`);
                        } else {
                            // Fallback to comma-separated
                            const arrayValue = decodedValue ? decodedValue.split(',').map(v => v.trim()).filter(v => v) : [];
                            flatConfig[actualKey] = arrayValue;
                        }
                    } catch (e) {
                        // Not JSON, use comma-separated
                        const arrayValue = actualValue ? actualValue.split(',').map(v => v.trim()).filter(v => v) : [];
                        flatConfig[actualKey] = arrayValue;
                        console.log(`Array field ${actualKey}: "${actualValue}" -> `, arrayValue);
                    }
                } else {
                    // Regular array: convert comma-separated string to array
                    const arrayValue = actualValue ? actualValue.split(',').map(v => v.trim()).filter(v => v) : [];
                    flatConfig[actualKey] = arrayValue;
                    console.log(`Array field ${actualKey}: "${actualValue}" -> `, arrayValue);
                }
            } else if (propType === 'integer') {
                flatConfig[actualKey] = parseInt(actualValue, 10);
            } else if (propType === 'number') {
                flatConfig[actualKey] = parseFloat(actualValue);
            } else if (propType === 'boolean') {
                const formElement = form.elements[actualKey] || form.elements[key];
                flatConfig[actualKey] = formElement ? formElement.checked : (actualValue === 'true' || actualValue === true);
            } else {
                flatConfig[actualKey] = actualValue;
            }
        } else {
            // No schema, try to infer type
            // Check if value looks like a JSON string (starts with [ or {)
            if (typeof actualValue === 'string' && (actualValue.trim().startsWith('[') || actualValue.trim().startsWith('{'))) {
                try {
                    // Handle HTML entity encoding
                    let decodedValue = actualValue;
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = actualValue;
                    decodedValue = tempDiv.textContent || tempDiv.innerText || actualValue;
                    
                    const parsed = JSON.parse(decodedValue);
                    flatConfig[actualKey] = parsed;
                    console.log(`No schema for ${actualKey}, but parsed as JSON:`, parsed);
                } catch (e) {
                    // Not valid JSON, save as string
                    flatConfig[actualKey] = actualValue;
                }
            } else {
                const formElement = form.elements[actualKey] || form.elements[key];
                if (formElement && formElement.type === 'checkbox') {
                    flatConfig[actualKey] = formElement.checked;
                } else {
                    flatConfig[actualKey] = actualValue;
                }
            }
        }
    }
    
    // Handle unchecked checkboxes (not in FormData) - including nested ones
    if (schema && schema.properties) {
        const allBoolFields = collectBooleanFields(schema);
        allBoolFields.forEach(key => {
            if (!(key in flatConfig)) {
                flatConfig[key] = false;
            }
        });
    }
    
    // Convert dot notation to nested object
    const config = dotToNested(flatConfig);
    
    console.log('Flat config:', flatConfig);
    console.log('Nested config to save:', config);
    
    // Save the configuration
    fetch('/api/v3/plugins/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            plugin_id: pluginId,
            config: config
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            // Hide validation errors on success
            displayValidationErrors([]);
            showNotification('Configuration saved successfully', 'success');
            closePluginConfigModal();
            loadInstalledPlugins(); // Refresh to show updated config
        } else {
            // Display validation errors if present
            if (data.validation_errors && Array.isArray(data.validation_errors)) {
                displayValidationErrors(data.validation_errors);
            }
            showNotification('Error saving configuration: ' + data.message, 'error');
        }
    })
    .catch(error => {
        console.error('Error saving plugin config:', error);
        showNotification('Error saving configuration: ' + error.message, 'error');
    });
}

function generatePluginConfigForm(pluginId, config) {
    console.log('[DEBUG] ===== Generating plugin config form =====');
    console.log('[DEBUG] Plugin ID:', pluginId);
    // Load plugin schema and actions for dynamic form generation
    return Promise.all([
        fetch(`/api/v3/plugins/schema?plugin_id=${pluginId}`).then(r => r.json()),
        fetch(`/api/v3/plugins/installed`).then(r => r.json())
    ])
        .then(([schemaData, pluginsData]) => {
            console.log('[DEBUG] Schema data received:', schemaData.status);
            
            // Get plugin info including web_ui_actions
            let pluginInfo = null;
            if (pluginsData.status === 'success' && pluginsData.data && pluginsData.data.plugins) {
                pluginInfo = pluginsData.data.plugins.find(p => p.id === pluginId);
                console.log('[DEBUG] Plugin info found:', pluginInfo ? 'yes' : 'no');
                if (pluginInfo) {
                    console.log('[DEBUG] Plugin info keys:', Object.keys(pluginInfo));
                    console.log('[DEBUG] web_ui_actions in pluginInfo:', 'web_ui_actions' in pluginInfo);
                    console.log('[DEBUG] web_ui_actions value:', pluginInfo.web_ui_actions);
                }
            } else {
                console.log('[DEBUG] pluginsData status:', pluginsData.status);
            }
            const webUiActions = pluginInfo ? (pluginInfo.web_ui_actions || []) : [];
            console.log('[DEBUG] Final webUiActions:', webUiActions, 'length:', webUiActions.length);
            
            if (schemaData.status === 'success' && schemaData.data.schema) {
                console.log('[DEBUG] Schema has properties:', Object.keys(schemaData.data.schema.properties || {}));
                // Store plugin ID, schema, and actions for form submission
                currentPluginConfig = {
                    pluginId: pluginId,
                    schema: schemaData.data.schema,
                    webUiActions: webUiActions
                };
                // Also update state
                currentPluginConfigState.schema = schemaData.data.schema;
                console.log('[DEBUG] Calling generateFormFromSchema...');
                return generateFormFromSchema(schemaData.data.schema, config, webUiActions);
            } else {
                // Fallback to simple form if no schema
                currentPluginConfig = { pluginId: pluginId, schema: null, webUiActions: webUiActions };
                return generateSimpleConfigForm(config, webUiActions);
            }
        })
        .catch(error => {
            console.error('Error loading schema:', error);
            currentPluginConfig = { pluginId: pluginId, schema: null, webUiActions: [] };
            return generateSimpleConfigForm(config, []);
        });
}

// Helper to flatten nested config for form display (converts {nfl: {enabled: true}} to {'nfl.enabled': true})
function flattenConfig(obj, prefix = '') {
    let result = {};
    
    for (const key in obj) {
        const value = obj[key];
        const fullKey = prefix ? `${prefix}.${key}` : key;
        
        if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
            // Recursively flatten nested objects
            Object.assign(result, flattenConfig(value, fullKey));
        } else {
            result[fullKey] = value;
        }
    }
    
    return result;
}

// Generate field HTML for a single property (used recursively)
function generateFieldHtml(key, prop, value, prefix = '') {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    const label = prop.title || key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    const description = prop.description || '';
    let html = '';

    // Handle patternProperties objects (dynamic key-value pairs like custom_feeds, feed_logo_map)
    if (prop.type === 'object' && prop.patternProperties && !prop.properties) {
        const fieldId = fullKey.replace(/\./g, '_');
        const currentValue = value || {};
        const patternProp = Object.values(prop.patternProperties)[0]; // Get the pattern property schema
        const valueType = patternProp.type || 'string';
        const maxProperties = prop.maxProperties || 50;
        const entries = Object.entries(currentValue);
        
        html += `
            <div class="key-value-pairs-container">
                <div class="mb-2">
                    <p class="text-sm text-gray-600 mb-2">${description || 'Add key-value pairs'}</p>
                    <div id="${fieldId}_pairs" class="space-y-2">
        `;
        
        // Render existing pairs
        entries.forEach(([pairKey, pairValue], index) => {
            html += `
                <div class="flex items-center gap-2 key-value-pair" data-index="${index}">
                    <input type="text" 
                           name="${fullKey}[key_${index}]" 
                           value="${pairKey}" 
                           placeholder="Key"
                           class="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                           data-key-index="${index}">
                    <input type="${valueType === 'string' ? 'text' : valueType === 'number' || valueType === 'integer' ? 'number' : 'text'}" 
                           name="${fullKey}[value_${index}]" 
                           value="${pairValue}" 
                           placeholder="Value"
                           class="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                           data-value-index="${index}">
                    <button type="button" 
                            onclick="removeKeyValuePair('${fieldId}', ${index})"
                            class="px-3 py-2 text-red-600 hover:text-red-800 hover:bg-red-50 rounded-md transition-colors"
                            title="Remove">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            `;
        });
        
        html += `
                    </div>
                    <button type="button" 
                            onclick="addKeyValuePair('${fieldId}', '${fullKey}', ${maxProperties})"
                            class="mt-2 px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md transition-colors"
                            ${entries.length >= maxProperties ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : ''}>
                        <i class="fas fa-plus mr-1"></i> Add Entry
                    </button>
                    <input type="hidden" id="${fieldId}_data" name="${fullKey}_data" value='${JSON.stringify(currentValue).replace(/'/g, "&#39;")}'>
                </div>
            </div>
        `;
        
        return html;
    }
    
    // Handle nested objects with known properties
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
                        class="w-full bg-gray-100 hover:bg-gray-200 px-4 py-3 flex items-center justify-between text-left transition-colors rounded-t-lg"
                        onclick="toggleNestedSection('${sectionId}', event); return false;"
                        data-section-id="${sectionId}">
                    <div class="flex-1">
                        <h4 class="font-semibold text-gray-900">${sectionLabel}</h4>
                        ${description ? `<p class="text-sm text-gray-600 mt-1">${description}</p>` : ''}
                    </div>
                    <i id="${sectionId}-icon" class="fas fa-chevron-right text-gray-500 transition-transform"></i>
                </button>
                <div id="${sectionId}" class="nested-content collapsed bg-gray-50 px-4 py-4 space-y-3 rounded-b-lg" style="max-height: 0; display: none;">
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
            const nestedValue = nestedConfig[nestedKey] !== undefined ? nestedConfig[nestedKey] : nestedProp.default;
            console.log(`[DEBUG] Processing nested field ${fullKey}.${nestedKey}:`, {
                type: nestedProp.type,
                hasXWidget: nestedProp.hasOwnProperty('x-widget'),
                xWidget: nestedProp['x-widget'],
                allKeys: Object.keys(nestedProp)
            });
            html += generateFieldHtml(nestedKey, nestedProp, nestedValue, fullKey);
        });
        
        html += `
                </div>
            </div>
        `;
        
        // Add extra spacing after nested sections to prevent overlap with next section
        html += `<div class="mb-4" style="clear: both;"></div>`;
        
        return html;
    }

    // Regular (non-nested) field
    html += `
        <div class="form-group">
            <label for="${fullKey}" class="block text-sm font-medium text-gray-700 mb-1">
                ${label}
            </label>
    `;

    if (description) {
        html += `<p class="text-sm text-gray-600 mb-2">${description}</p>`;
    }

    // Generate appropriate input based on type
    if (prop.type === 'boolean') {
        html += `
            <label class="flex items-center">
                <input type="checkbox" id="${fullKey}" name="${fullKey}" ${value ? 'checked' : ''} class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                <span class="ml-2 text-sm">Enabled</span>
            </label>
        `;
    } else if (prop.type === 'number' || prop.type === 'integer') {
        const min = prop.minimum !== undefined ? `min="${prop.minimum}"` : '';
        const max = prop.maximum !== undefined ? `max="${prop.maximum}"` : '';
        const step = prop.type === 'integer' ? 'step="1"' : 'step="any"';
        
        // Ensure value respects min/max constraints
        let fieldValue = value !== undefined ? value : (prop.default !== undefined ? prop.default : '');
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
        
        // If still empty and we have a default, use it
        if (fieldValue === '' && prop.default !== undefined) {
            fieldValue = prop.default;
        }
        
        html += `
            <input type="number" id="${fullKey}" name="${fullKey}" value="${fieldValue}" ${min} ${max} ${step} class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
        `;
    } else if (prop.type === 'array') {
        // Check if this is a file upload widget - try multiple ways to access x-widget
        const hasXWidget = prop.hasOwnProperty('x-widget');
        const xWidgetValue = prop['x-widget'];
        const xWidgetValue2 = prop['x-widget'] || prop['x_widget'] || prop.xWidget;
        
        console.log(`[DEBUG] Array field ${fullKey}:`, {
            type: prop.type,
            hasXWidget: hasXWidget,
            'x-widget': xWidgetValue,
            'x-widget (alt)': xWidgetValue2,
            'x-upload-config': prop['x-upload-config'],
            propKeys: Object.keys(prop),
            propString: JSON.stringify(prop),
            value: value
        });
        
        // Check for file-upload widget - be more defensive
        if (xWidgetValue === 'file-upload' || xWidgetValue2 === 'file-upload') {
            console.log(`[DEBUG] ✅ Detected file-upload widget for ${fullKey} - rendering upload zone`);
            const uploadConfig = prop['x-upload-config'] || {};
            const pluginId = uploadConfig.plugin_id || currentPluginConfig?.pluginId || 'static-image';
            const maxFiles = uploadConfig.max_files || 10;
            const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/bmp', 'image/gif'];
            const maxSizeMB = uploadConfig.max_size_mb || 5;
            
            const currentImages = Array.isArray(value) ? value : [];
            const fieldId = fullKey.replace(/\./g, '_');
            
            html += `
                <div id="${fieldId}_upload_widget" class="mt-1">
                    <!-- File Upload Drop Zone -->
                    <div id="${fieldId}_drop_zone" 
                         class="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-blue-400 transition-colors cursor-pointer"
                         ondrop="handleFileDrop(event, '${fieldId}')" 
                         ondragover="event.preventDefault()" 
                         onclick="document.getElementById('${fieldId}_file_input').click()">
                        <input type="file" 
                               id="${fieldId}_file_input" 
                               multiple 
                               accept="${allowedTypes.join(',')}"
                               style="display: none;"
                               onchange="handleFileSelect(event, '${fieldId}')">
                        <i class="fas fa-cloud-upload-alt text-3xl text-gray-400 mb-2"></i>
                        <p class="text-sm text-gray-600">Drag and drop images here or click to browse</p>
                        <p class="text-xs text-gray-500 mt-1">Max ${maxFiles} files, ${maxSizeMB}MB each (PNG, JPG, GIF, BMP)</p>
                    </div>
                    
                    <!-- Uploaded Images List -->
                    <div id="${fieldId}_image_list" class="mt-4 space-y-2">
                        ${currentImages.map((img, idx) => {
                            const imgSchedule = img.schedule || {};
                            const hasSchedule = imgSchedule.enabled && imgSchedule.mode && imgSchedule.mode !== 'always';
                            const scheduleSummary = hasSchedule ? getScheduleSummary(imgSchedule) : 'Always shown';
                            
                            return `
                            <div id="img_${img.id || idx}" class="bg-gray-50 p-3 rounded-lg border border-gray-200">
                                <div class="flex items-center justify-between mb-2">
                                    <div class="flex items-center space-x-3 flex-1">
                                        <img src="/${img.path || ''}" 
                                             alt="${img.filename || ''}" 
                                             class="w-16 h-16 object-cover rounded"
                                             onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                                        <div style="display:none;" class="w-16 h-16 bg-gray-200 rounded flex items-center justify-center">
                                            <i class="fas fa-image text-gray-400"></i>
                                        </div>
                                        <div class="flex-1 min-w-0">
                                            <p class="text-sm font-medium text-gray-900 truncate">${img.original_filename || img.filename || 'Image'}</p>
                                            <p class="text-xs text-gray-500">${formatFileSize(img.size || 0)} • ${formatDate(img.uploaded_at)}</p>
                                            <p class="text-xs text-blue-600 mt-1">
                                                <i class="fas fa-clock mr-1"></i>${scheduleSummary}
                                            </p>
                                        </div>
                                    </div>
                                    <div class="flex items-center space-x-2 ml-4">
                                        <button type="button" 
                                                onclick="openImageSchedule('${fieldId}', '${img.id}', ${idx})"
                                                class="text-blue-600 hover:text-blue-800 p-2" 
                                                title="Schedule this image">
                                            <i class="fas fa-calendar-alt"></i>
                                        </button>
                                        <button type="button" 
                                                onclick="deleteUploadedImage('${fieldId}', '${img.id}', '${pluginId}')"
                                                class="text-red-600 hover:text-red-800 p-2"
                                                title="Delete image">
                                            <i class="fas fa-trash"></i>
                                        </button>
                                    </div>
                                </div>
                                <!-- Schedule widget will be inserted here when opened -->
                                <div id="schedule_${img.id || idx}" class="hidden mt-3 pt-3 border-t border-gray-300"></div>
                            </div>
                            `;
                        }).join('')}
                    </div>
                    
                    <!-- Hidden input to store image data -->
                    <input type="hidden" id="${fieldId}_images_data" name="${fullKey}" value="${JSON.stringify(currentImages).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')}">
                </div>
            `;
        } else if (xWidgetValue === 'checkbox-group' || xWidgetValue2 === 'checkbox-group') {
            // Checkbox group widget for multi-select arrays with enum items
            console.log(`[DEBUG] ✅ Detected checkbox-group widget for ${fullKey} - rendering checkboxes`);
            const arrayValue = Array.isArray(value) ? value : (prop.default || []);
            const enumItems = prop.items && prop.items.enum ? prop.items.enum : [];
            const xOptions = prop['x-options'] || {};
            const labels = xOptions.labels || {};
            
            html += `<div class="mt-1 space-y-2">`;
            enumItems.forEach(option => {
                const isChecked = arrayValue.includes(option);
                const label = labels[option] || option.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                const checkboxId = `${fullKey.replace(/\./g, '_')}_${option}`;
                html += `
                    <label class="flex items-center">
                        <input type="checkbox" 
                               id="${checkboxId}" 
                               name="${fullKey}[]" 
                               value="${option}" 
                               ${isChecked ? 'checked' : ''} 
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <span class="ml-2 text-sm text-gray-700">${label}</span>
                    </label>
                `;
            });
            html += `</div>`;
        } else {
            // Regular array input
            console.log(`[DEBUG] ❌ NOT a file upload widget for ${fullKey}, using regular array input`);
        const arrayValue = Array.isArray(value) ? value.join(', ') : '';
        html += `
            <input type="text" id="${fullKey}" name="${fullKey}" value="${arrayValue}" placeholder="Enter values separated by commas" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
            <p class="text-sm text-gray-600 mt-1">Enter values separated by commas</p>
        `;
        }
    } else if (prop.enum) {
        html += `<select id="${fullKey}" name="${fullKey}" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">`;
        prop.enum.forEach(option => {
            const selected = value === option ? 'selected' : '';
            html += `<option value="${option}" ${selected}>${option}</option>`;
        });
        html += `</select>`;
    } else {
        // Check if this is a secret field
        const isSecret = prop['x-secret'] === true;
        const inputType = isSecret ? 'password' : 'text';
        const maxLength = prop.maxLength || '';
        const maxLengthAttr = maxLength ? `maxlength="${maxLength}"` : '';
        const secretClass = isSecret ? 'pr-10' : '';
        
        html += `
            <div class="relative">
                <input type="${inputType}" id="${fullKey}" name="${fullKey}" value="${value !== undefined ? value : ''}" ${maxLengthAttr} class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm ${secretClass}">
        `;
        
        if (isSecret) {
            html += `
                <button type="button" onclick="togglePasswordVisibility('${fullKey}')" class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-500 hover:text-gray-700">
                    <i id="${fullKey}-icon" class="fas fa-eye"></i>
                </button>
            `;
        }
        
        html += `</div>`;
    }

    html += `</div>`;
    
    return html;
}

function generateFormFromSchema(schema, config, webUiActions = []) {
    console.log('[DEBUG] ===== generateFormFromSchema called =====');
    console.log('[DEBUG] Schema properties:', Object.keys(schema.properties || {}));
    console.log('[DEBUG] Web UI Actions:', webUiActions.length);
    let formHtml = '<form id="plugin-config-form" class="space-y-4" novalidate>';

    if (schema.properties) {
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

            const value = config[key] !== undefined ? config[key] : prop.default;
            formHtml += generateFieldHtml(key, prop, value);
        });
    }

    // Add web UI actions section if plugin defines any
    console.log('[DEBUG] webUiActions:', webUiActions, 'length:', webUiActions ? webUiActions.length : 0);
    if (webUiActions && webUiActions.length > 0) {
        console.log('[DEBUG] Rendering', webUiActions.length, 'actions');
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
            
            // Map color names to explicit Tailwind classes to ensure they're included
            const colorMap = {
                'blue': { bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-900', textLight: 'text-blue-700', btn: 'bg-blue-600 hover:bg-blue-700' },
                'green': { bg: 'bg-green-50', border: 'border-green-200', text: 'text-green-900', textLight: 'text-green-700', btn: 'bg-green-600 hover:bg-green-700' },
                'red': { bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-900', textLight: 'text-red-700', btn: 'bg-red-600 hover:bg-red-700' },
                'yellow': { bg: 'bg-yellow-50', border: 'border-yellow-200', text: 'text-yellow-900', textLight: 'text-yellow-700', btn: 'bg-yellow-600 hover:bg-yellow-700' },
                'purple': { bg: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-900', textLight: 'text-purple-700', btn: 'bg-purple-600 hover:bg-purple-700' }
            };
            
            const colors = colorMap[bgColor] || colorMap['blue'];
            
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
                                    onclick="executePluginAction('${action.id}', ${index})" 
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
    } else {
        console.log('[DEBUG] No webUiActions to render');
    }

    formHtml += `
        <div class="flex justify-end space-x-2 pt-4 border-t border-gray-200">
            <button type="button" onclick="closePluginConfigModal()" class="btn bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md">
                Cancel
            </button>
            <button type="submit" class="btn bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md">
                <i class="fas fa-save mr-2"></i>Save Configuration
            </button>
        </div>
    </form>
    `;

    return Promise.resolve(formHtml);
}

// Functions to handle patternProperties key-value pairs
window.addKeyValuePair = function(fieldId, fullKey, maxProperties) {
    const pairsContainer = document.getElementById(fieldId + '_pairs');
    if (!pairsContainer) return;
    
    const currentPairs = pairsContainer.querySelectorAll('.key-value-pair');
    if (currentPairs.length >= maxProperties) {
        alert(`Maximum ${maxProperties} entries allowed`);
        return;
    }
    
    const newIndex = currentPairs.length;
    const valueType = 'string'; // Default to string, could be determined from schema
    
    const pairHtml = `
        <div class="flex items-center gap-2 key-value-pair" data-index="${newIndex}">
            <input type="text" 
                   name="${fullKey}[key_${newIndex}]" 
                   value="" 
                   placeholder="Key"
                   class="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                   data-key-index="${newIndex}"
                   onchange="updateKeyValuePairData('${fieldId}', '${fullKey}')">
            <input type="text" 
                   name="${fullKey}[value_${newIndex}]" 
                   value="" 
                   placeholder="Value"
                   class="flex-1 px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                   data-value-index="${newIndex}"
                   onchange="updateKeyValuePairData('${fieldId}', '${fullKey}')">
            <button type="button" 
                    onclick="removeKeyValuePair('${fieldId}', ${newIndex})"
                    class="px-3 py-2 text-red-600 hover:text-red-800 hover:bg-red-50 rounded-md transition-colors"
                    title="Remove">
                <i class="fas fa-trash"></i>
            </button>
        </div>
    `;
    
    pairsContainer.insertAdjacentHTML('beforeend', pairHtml);
    updateKeyValuePairData(fieldId, fullKey);
    
    // Update add button state
    const addButton = pairsContainer.nextElementSibling;
    if (addButton && currentPairs.length + 1 >= maxProperties) {
        addButton.disabled = true;
        addButton.style.opacity = '0.5';
        addButton.style.cursor = 'not-allowed';
    }
};

window.removeKeyValuePair = function(fieldId, index) {
    const pairsContainer = document.getElementById(fieldId + '_pairs');
    if (!pairsContainer) return;
    
    const pair = pairsContainer.querySelector(`.key-value-pair[data-index="${index}"]`);
    if (pair) {
        pair.remove();
        // Re-index remaining pairs
        const remainingPairs = pairsContainer.querySelectorAll('.key-value-pair');
        remainingPairs.forEach((p, newIndex) => {
            p.setAttribute('data-index', newIndex);
            const keyInput = p.querySelector('[data-key-index]');
            const valueInput = p.querySelector('[data-value-index]');
            if (keyInput) {
                keyInput.setAttribute('name', keyInput.getAttribute('name').replace(/\[key_\d+\]/, `[key_${newIndex}]`));
                keyInput.setAttribute('data-key-index', newIndex);
                keyInput.setAttribute('onchange', `updateKeyValuePairData('${fieldId}', '${keyInput.getAttribute('name').split('[')[0]}')`);
            }
            if (valueInput) {
                valueInput.setAttribute('name', valueInput.getAttribute('name').replace(/\[value_\d+\]/, `[value_${newIndex}]`));
                valueInput.setAttribute('data-value-index', newIndex);
                valueInput.setAttribute('onchange', `updateKeyValuePairData('${fieldId}', '${valueInput.getAttribute('name').split('[')[0]}')`);
            }
            const removeButton = p.querySelector('button[onclick*="removeKeyValuePair"]');
            if (removeButton) {
                removeButton.setAttribute('onclick', `removeKeyValuePair('${fieldId}', ${newIndex})`);
            }
        });
        const hiddenInput = pairsContainer.closest('.key-value-pairs-container').querySelector('input[type="hidden"]');
        if (hiddenInput) {
            const hiddenName = hiddenInput.getAttribute('name').replace(/_data$/, '');
            updateKeyValuePairData(fieldId, hiddenName);
        }
        
        // Update add button state
        const addButton = pairsContainer.nextElementSibling;
        if (addButton) {
            const maxProperties = parseInt(addButton.getAttribute('onclick').match(/\d+/)[0]);
            if (remainingPairs.length < maxProperties) {
                addButton.disabled = false;
                addButton.style.opacity = '1';
                addButton.style.cursor = 'pointer';
            }
        }
    }
};

window.updateKeyValuePairData = function(fieldId, fullKey) {
    const pairsContainer = document.getElementById(fieldId + '_pairs');
    const hiddenInput = document.getElementById(fieldId + '_data');
    if (!pairsContainer || !hiddenInput) return;
    
    const pairs = {};
    const keyInputs = pairsContainer.querySelectorAll('[data-key-index]');
    const valueInputs = pairsContainer.querySelectorAll('[data-value-index]');
    
    keyInputs.forEach((keyInput, idx) => {
        const key = keyInput.value.trim();
        const valueInput = Array.from(valueInputs).find(v => v.getAttribute('data-value-index') === keyInput.getAttribute('data-key-index'));
        if (key && valueInput) {
            const value = valueInput.value.trim();
            if (value) {
                pairs[key] = value;
            }
        }
    });
    
    hiddenInput.value = JSON.stringify(pairs);
};

// Function to toggle nested sections
window.toggleNestedSection = function(sectionId, event) {
    // Prevent event bubbling if event is provided
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    
    const content = document.getElementById(sectionId);
    const icon = document.getElementById(sectionId + '-icon');
    
    if (!content || !icon) return;
    
    // Prevent multiple simultaneous toggles
    if (content.dataset.toggling === 'true') {
        return;
    }
    
    // Mark as toggling
    content.dataset.toggling = 'true';
    
    // Check current state before making changes
    const hasCollapsed = content.classList.contains('collapsed');
    const hasExpanded = content.classList.contains('expanded');
    const displayStyle = content.style.display;
    const computedDisplay = window.getComputedStyle(content).display;
    
    // Check if content is currently collapsed - prioritize class over display style
    const isCollapsed = hasCollapsed || (!hasExpanded && (displayStyle === 'none' || computedDisplay === 'none'));
    
    if (isCollapsed) {
        // Expand the section
        content.classList.remove('collapsed');
        content.classList.add('expanded');
        content.style.display = 'block';
        // Force reflow to ensure transition works
        content.offsetHeight;
        content.style.maxHeight = content.scrollHeight + 'px';
        icon.classList.remove('fa-chevron-right');
        icon.classList.add('fa-chevron-down');
        
        // Allow parent section to show overflow when expanded
        const sectionElement = content.closest('.nested-section');
        if (sectionElement) {
            sectionElement.style.overflow = 'visible';
        }
        
        // After animation completes, remove max-height constraint to allow natural expansion
        // This allows parent sections to automatically expand
        setTimeout(() => {
            // Only set to none if still expanded (prevent race condition)
            if (content.classList.contains('expanded') && !content.classList.contains('collapsed')) {
                content.style.maxHeight = 'none';
            }
            // Clear toggling flag
            content.dataset.toggling = 'false';
        }, 300); // Match CSS transition duration
        
        // Scroll the expanded content into view after a short delay to allow animation
        setTimeout(() => {
            if (sectionElement) {
                // Find the modal container
                const modalContent = sectionElement.closest('.modal-content');
                if (modalContent) {
                    // Scroll the section header into view within the modal
                    const headerButton = sectionElement.querySelector('button');
                    if (headerButton) {
                        headerButton.scrollIntoView({ behavior: 'smooth', block: 'start', inline: 'nearest' });
                    }
                } else {
                    // If not in a modal, just scroll the section
                    sectionElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        }, 350); // Wait for animation to complete
    } else {
        // Collapse the section
        content.classList.add('collapsed');
        content.classList.remove('expanded');
        content.style.maxHeight = content.scrollHeight + 'px';
        // Force reflow
        content.offsetHeight;
        content.style.maxHeight = '0';
        // Restore parent section overflow when collapsed
        const sectionElement = content.closest('.nested-section');
        if (sectionElement) {
            sectionElement.style.overflow = 'hidden';
        }
        
        // Use setTimeout to set display:none after transition completes
        setTimeout(() => {
            if (content.classList.contains('collapsed')) {
                content.style.display = 'none';
            }
            // Clear toggling flag
            content.dataset.toggling = 'false';
        }, 300); // Match the CSS transition duration
        icon.classList.remove('fa-chevron-down');
        icon.classList.add('fa-chevron-right');
    }
}

function generateSimpleConfigForm(config, webUiActions = []) {
    console.log('[DEBUG] generateSimpleConfigForm - webUiActions:', webUiActions, 'length:', webUiActions ? webUiActions.length : 0);
    let actionsHtml = '';
    if (webUiActions && webUiActions.length > 0) {
        console.log('[DEBUG] Rendering', webUiActions.length, 'actions in simple form');
        actionsHtml = `
            <div class="border-t border-gray-200 pt-4 mt-4">
                <h3 class="text-lg font-semibold text-gray-900 mb-3">Actions</h3>
                <div class="space-y-3">
        `;
        
        // Map color names to explicit Tailwind classes
        const colorMap = {
            'blue': { bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-900', textLight: 'text-blue-700', btn: 'bg-blue-600 hover:bg-blue-700' },
            'green': { bg: 'bg-green-50', border: 'border-green-200', text: 'text-green-900', textLight: 'text-green-700', btn: 'bg-green-600 hover:bg-green-700' },
            'red': { bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-900', textLight: 'text-red-700', btn: 'bg-red-600 hover:bg-red-700' },
            'yellow': { bg: 'bg-yellow-50', border: 'border-yellow-200', text: 'text-yellow-900', textLight: 'text-yellow-700', btn: 'bg-yellow-600 hover:bg-yellow-700' },
            'purple': { bg: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-900', textLight: 'text-purple-700', btn: 'bg-purple-600 hover:bg-purple-700' }
        };
        
        webUiActions.forEach((action, index) => {
            const actionId = `action-${action.id}-${index}`;
            const statusId = `action-status-${action.id}-${index}`;
            const bgColor = action.color || 'blue';
            const colors = colorMap[bgColor] || colorMap['blue'];
            
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
                                    onclick="executePluginAction('${action.id}', ${index})" 
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
        <form id="plugin-config-form" class="space-y-4" novalidate>
            <div class="form-group">
                <label class="block text-sm font-medium text-gray-700 mb-1">Configuration</label>
                <textarea name="config" class="form-control h-32" placeholder="Plugin configuration JSON">${JSON.stringify(config, null, 2)}</textarea>
            </div>
            ${actionsHtml}
            <div class="flex justify-end space-x-2">
                <button type="button" onclick="closePluginConfigModal()" class="btn bg-gray-600 hover:bg-gray-700 text-white px-4 py-2">
                    Cancel
                </button>
                <button type="submit" class="btn bg-blue-600 hover:bg-blue-700 text-white px-4 py-2">
                    Save Configuration
                </button>
            </div>
        </form>
    `;
}

// Plugin config modal state
let currentPluginConfigState = {
    pluginId: null,
    config: {},
    schema: null,
    jsonEditor: null,
    formData: {}
};

// Initialize JSON editor
function initJsonEditor() {
    const textarea = document.getElementById('plugin-config-json-editor');
    if (!textarea) return null;
    
    // Check if CodeMirror is available
    if (typeof CodeMirror === 'undefined') {
        console.error('CodeMirror not loaded. Please refresh the page.');
        showNotification('JSON editor not available. Please refresh the page.', 'error');
        return null;
    }
    
    if (currentPluginConfigState.jsonEditor) {
        currentPluginConfigState.jsonEditor.toTextArea();
        currentPluginConfigState.jsonEditor = null;
    }
    
    const editor = CodeMirror.fromTextArea(textarea, {
        mode: 'application/json',
        theme: 'monokai',
        lineNumbers: true,
        lineWrapping: true,
        indentUnit: 2,
        tabSize: 2,
        autoCloseBrackets: true,
        matchBrackets: true,
        foldGutter: true,
        gutters: ['CodeMirror-linenumbers', 'CodeMirror-foldgutter']
    });
    
    // Validate JSON on change
    editor.on('change', function() {
        const value = editor.getValue();
        try {
            JSON.parse(value);
            editor.setOption('class', '');
        } catch (e) {
            editor.setOption('class', 'cm-error');
        }
    });
    
    return editor;
}

// Switch between form and JSON views
function switchPluginConfigView(view) {
    const formView = document.getElementById('plugin-config-form-view');
    const jsonView = document.getElementById('plugin-config-json-view');
    const formBtn = document.getElementById('view-toggle-form');
    const jsonBtn = document.getElementById('view-toggle-json');
    
    if (view === 'json') {
        formView.classList.add('hidden');
        jsonView.classList.remove('hidden');
        formBtn.classList.remove('active', 'bg-blue-600', 'text-white');
        formBtn.classList.add('text-gray-700', 'hover:bg-gray-200');
        jsonBtn.classList.add('active', 'bg-blue-600', 'text-white');
        jsonBtn.classList.remove('text-gray-700', 'hover:bg-gray-200');
        
        // Sync form data to JSON editor
        syncFormToJson();
        
        // Initialize editor if not already done
        if (!currentPluginConfigState.jsonEditor) {
            // Small delay to ensure textarea is visible
            setTimeout(() => {
                currentPluginConfigState.jsonEditor = initJsonEditor();
                if (currentPluginConfigState.jsonEditor) {
                    const jsonText = JSON.stringify(currentPluginConfigState.config, null, 2);
                    currentPluginConfigState.jsonEditor.setValue(jsonText);
                    currentPluginConfigState.jsonEditor.refresh();
                }
            }, 50);
        } else {
            // Update editor content if already initialized
            const jsonText = JSON.stringify(currentPluginConfigState.config, null, 2);
            currentPluginConfigState.jsonEditor.setValue(jsonText);
            currentPluginConfigState.jsonEditor.refresh();
        }
    } else {
        jsonView.classList.add('hidden');
        formView.classList.remove('hidden');
        jsonBtn.classList.remove('active', 'bg-blue-600', 'text-white');
        jsonBtn.classList.add('text-gray-700', 'hover:bg-gray-200');
        formBtn.classList.add('active', 'bg-blue-600', 'text-white');
        formBtn.classList.remove('text-gray-700', 'hover:bg-gray-200');
        
        // Sync JSON to form if JSON was edited
        syncJsonToForm();
    }
}

// Sync form data to JSON config
function syncFormToJson() {
    const form = document.getElementById('plugin-config-form');
    if (!form) return;
    
    const formData = new FormData(form);
    const config = {};
    
    // Get schema for type conversion
    const schema = currentPluginConfigState.schema;
    
    for (let [key, value] of formData.entries()) {
        if (key === 'enabled') continue; // Skip enabled, managed separately
        
        // Handle nested keys (dot notation)
        const keys = key.split('.');
        let current = config;
        for (let i = 0; i < keys.length - 1; i++) {
            if (!current[keys[i]]) {
                current[keys[i]] = {};
            }
            current = current[keys[i]];
        }
        
        const finalKey = keys[keys.length - 1];
        const prop = schema?.properties?.[finalKey] || (keys.length > 1 ? null : schema?.properties?.[key]);
        
        // Type conversion based on schema
        if (prop?.type === 'array') {
            current[finalKey] = value.split(',').map(item => item.trim()).filter(item => item.length > 0);
        } else if (prop?.type === 'integer' || key === 'display_duration') {
            current[finalKey] = parseInt(value) || 0;
        } else if (prop?.type === 'number') {
            current[finalKey] = parseFloat(value) || 0;
        } else if (prop?.type === 'boolean') {
            current[finalKey] = value === 'true' || value === true;
        } else {
            current[finalKey] = value;
        }
    }
    
    // Deep merge with existing config to preserve nested structures
    function deepMerge(target, source) {
        for (const key in source) {
            if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
                if (!target[key] || typeof target[key] !== 'object' || Array.isArray(target[key])) {
                    target[key] = {};
                }
                deepMerge(target[key], source[key]);
            } else {
                target[key] = source[key];
            }
        }
        return target;
    }
    
    // Deep merge new form data into existing config
    currentPluginConfigState.config = deepMerge(
        JSON.parse(JSON.stringify(currentPluginConfigState.config)), // Deep clone
        config
    );
}

// Sync JSON editor content to form
function syncJsonToForm() {
    if (!currentPluginConfigState.jsonEditor) return;
    
    try {
        const jsonText = currentPluginConfigState.jsonEditor.getValue();
        const config = JSON.parse(jsonText);
        currentPluginConfigState.config = config;
        
        // Update form fields (this is complex, so we'll reload the form)
        // For now, just update the config state - form will be regenerated on next open
        console.log('JSON synced to config state');
    } catch (e) {
        console.error('Invalid JSON in editor:', e);
        showNotification('Invalid JSON in editor. Please fix errors before switching views.', 'error');
    }
}

// Reset plugin config to defaults
async function resetPluginConfigToDefaults() {
    if (!currentPluginConfigState.pluginId) {
        showNotification('No plugin selected', 'error');
        return;
    }
    
    if (!confirm('Are you sure you want to reset this plugin configuration to defaults? This will replace all current settings.')) {
        return;
    }
    
    try {
        const response = await fetch('/api/v3/plugins/config/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                plugin_id: currentPluginConfigState.pluginId,
                preserve_secrets: true
            })
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            
            // Reload the config form with defaults
            const newConfig = data.data?.config || {};
            currentPluginConfigState.config = newConfig;
            
            // Regenerate form
            const content = document.getElementById('plugin-config-content');
            if (content) {
                content.innerHTML = '<div class="flex items-center justify-center py-8"><i class="fas fa-spinner fa-spin text-2xl text-blue-600"></i></div>';
                generatePluginConfigForm(currentPluginConfigState.pluginId, newConfig)
                    .then(formHtml => {
                        content.innerHTML = formHtml;
                        const form = document.getElementById('plugin-config-form');
                        if (form) {
                            form.addEventListener('submit', handlePluginConfigSubmit);
                        }
                    });
            }
            
            // Update JSON editor if it's visible
            if (currentPluginConfigState.jsonEditor) {
                const jsonText = JSON.stringify(newConfig, null, 2);
                currentPluginConfigState.jsonEditor.setValue(jsonText);
            }
        } else {
            showNotification(data.message || 'Failed to reset configuration', 'error');
        }
    } catch (error) {
        console.error('Error resetting config:', error);
        showNotification('Error resetting configuration: ' + error.message, 'error');
    }
}

// Display validation errors
function displayValidationErrors(errors) {
    const errorContainer = document.getElementById('plugin-config-validation-errors');
    const errorList = document.getElementById('validation-errors-list');
    
    if (!errorContainer || !errorList) return;
    
    if (errors && errors.length > 0) {
        errorContainer.classList.remove('hidden');
        errorList.innerHTML = errors.map(error => `<li>${escapeHtml(error)}</li>`).join('');
    } else {
        errorContainer.classList.add('hidden');
        errorList.innerHTML = '';
    }
}

// Save configuration from JSON editor
async function saveConfigFromJsonEditor() {
    if (!currentPluginConfigState.jsonEditor || !currentPluginConfigState.pluginId) {
        return;
    }
    
    try {
        const jsonText = currentPluginConfigState.jsonEditor.getValue();
        const config = JSON.parse(jsonText);
        
        // Update state
        currentPluginConfigState.config = config;
        
        // Save the configuration (will handle validation errors)
        savePluginConfiguration(currentPluginConfigState.pluginId, config);
    } catch (e) {
        console.error('Error saving JSON config:', e);
        if (e instanceof SyntaxError) {
            showNotification('Invalid JSON. Please fix syntax errors before saving.', 'error');
            displayValidationErrors([`JSON Syntax Error: ${e.message}`]);
        } else {
            showNotification('Error saving configuration: ' + e.message, 'error');
        }
    }
}

window.closePluginConfigModal = function() {
        const modal = document.getElementById('plugin-config-modal');
    modal.style.display = 'none';
    
    // Clean up JSON editor
    if (currentPluginConfigState.jsonEditor) {
        currentPluginConfigState.jsonEditor.toTextArea();
        currentPluginConfigState.jsonEditor = null;
    }
    
    // Reset state
    currentPluginConfig = null;
    currentPluginConfigState.pluginId = null;
    currentPluginConfigState.config = {};
    currentPluginConfigState.schema = null;
    
    // Hide validation errors
    displayValidationErrors([]);
    
    console.log('Modal closed');
}

// Generic Plugin Action Handler
window.executePluginAction = function(actionId, actionIndex, pluginIdParam = null) {
    // Get plugin ID from parameter, currentPluginConfig, or try to find from context
    let pluginId = pluginIdParam || currentPluginConfig?.pluginId;
    
    // If still no pluginId, try to find it from the button's context or Alpine.js
    if (!pluginId) {
        // Try to get from Alpine.js context if we're in a plugin tab
        if (window.Alpine && document.querySelector('[x-data*="plugin"]')) {
            const pluginTab = document.querySelector(`[x-show*="activeTab === plugin.id"]`);
            if (pluginTab) {
                const pluginData = Alpine.$data(pluginTab.closest('[x-data]'));
                if (pluginData && pluginData.plugin) {
                    pluginId = pluginData.plugin.id;
                }
            }
        }
    }
    
    if (!pluginId) {
        console.error('No plugin ID available. actionId:', actionId, 'actionIndex:', actionIndex);
        if (typeof showNotification === 'function') {
            showNotification('Unable to determine plugin ID. Please refresh the page.', 'error');
        }
        return;
    }
    
    console.log('[DEBUG] executePluginAction - pluginId:', pluginId, 'actionId:', actionId, 'actionIndex:', actionIndex);
    
    const actionIdFull = `action-${actionId}-${actionIndex}`;
    const statusId = `action-status-${actionId}-${actionIndex}`;
    const btn = document.getElementById(actionIdFull);
    const statusDiv = document.getElementById(statusId);
    
    if (!btn || !statusDiv) {
        console.error(`Action elements not found: ${actionIdFull}`);
        return;
    }
    
    // Get action definition - try currentPluginConfig first, then fetch from API
    let action = currentPluginConfig?.webUiActions?.[actionIndex];
    
    if (!action) {
        // Try to get from installed plugins
        if (window.installedPlugins) {
            const plugin = window.installedPlugins.find(p => p.id === pluginId);
            if (plugin && plugin.web_ui_actions) {
                action = plugin.web_ui_actions[actionIndex];
            }
        }
    }
    
    if (!action) {
        console.error(`Action not found: ${actionId} for plugin ${pluginId}`);
        console.log('[DEBUG] currentPluginConfig:', currentPluginConfig);
        console.log('[DEBUG] installedPlugins:', window.installedPlugins);
        if (typeof showNotification === 'function') {
            showNotification(`Action ${actionId} not found. Please refresh the page.`, 'error');
        }
        return;
    }
    
    console.log('[DEBUG] Found action:', action);
    
    // Check if we're in step 2 (completing OAuth flow)
    if (btn.dataset.step === '2') {
        const redirectUrl = prompt(action.step2_prompt || 'Please paste the full redirect URL:');
        if (!redirectUrl || !redirectUrl.trim()) {
            return;
        }
        
        // Complete authentication
        btn.disabled = true;
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Completing...';
        statusDiv.classList.remove('hidden');
        statusDiv.innerHTML = '<div class="text-blue-600"><i class="fas fa-spinner fa-spin mr-2"></i>Completing authentication...</div>';
        
        fetch('/api/v3/plugins/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                plugin_id: pluginId,
                action_id: actionId,
                params: {step: '2', redirect_url: redirectUrl.trim()}
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                statusDiv.innerHTML = `<div class="text-green-600"><i class="fas fa-check-circle mr-2"></i>${data.message}</div>`;
                btn.innerHTML = originalText;
                btn.disabled = false;
                delete btn.dataset.step;
                if (typeof showNotification === 'function') {
                    showNotification(data.message || 'Action completed successfully!', 'success');
                }
            } else {
                statusDiv.innerHTML = `<div class="text-red-600"><i class="fas fa-exclamation-circle mr-2"></i>${data.message}</div>`;
                if (data.output) {
                    statusDiv.innerHTML += `<pre class="mt-2 text-xs bg-red-50 p-2 rounded overflow-auto max-h-32">${data.output}</pre>`;
                }
                btn.innerHTML = originalText;
                btn.disabled = false;
                delete btn.dataset.step;
            }
        })
        .catch(error => {
            statusDiv.innerHTML = `<div class="text-red-600"><i class="fas fa-exclamation-circle mr-2"></i>Error: ${error.message}</div>`;
            btn.innerHTML = originalText;
            btn.disabled = false;
            delete btn.dataset.step;
        });
        return;
    }
    
    // Step 1: Execute action
    btn.disabled = true;
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Executing...';
    statusDiv.classList.remove('hidden');
    statusDiv.innerHTML = '<div class="text-blue-600"><i class="fas fa-spinner fa-spin mr-2"></i>Executing action...</div>';
    
    fetch('/api/v3/plugins/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            plugin_id: pluginId,
            action_id: actionId,
            params: {}
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            if (data.requires_step2 && data.auth_url) {
                // OAuth flow - show auth URL
                statusDiv.innerHTML = `
                    <div class="bg-blue-50 border border-blue-200 rounded p-3">
                        <div class="text-blue-900 font-medium mb-2">
                            <i class="fas fa-link mr-2"></i>${data.message || 'Authorization URL Generated'}
                        </div>
                        <div class="mb-3">
                            <p class="text-sm text-blue-700 mb-2">1. Click the link below to authorize:</p>
                            <a href="${data.auth_url}" target="_blank" class="text-blue-600 hover:text-blue-800 underline break-all">
                                ${data.auth_url}
                            </a>
                        </div>
                        <div class="mb-2">
                            <p class="text-sm text-blue-700 mb-2">2. After authorization, copy the FULL redirect URL from your browser.</p>
                            <p class="text-sm text-blue-600">3. Click the button again and paste the redirect URL when prompted.</p>
                        </div>
                    </div>
                `;
                btn.innerHTML = action.step2_button_text || 'Complete Authentication';
                btn.dataset.step = '2';
                btn.disabled = false;
                if (typeof showNotification === 'function') {
                    showNotification(data.message || 'Authorization URL generated. Please authorize and paste the redirect URL.', 'info');
                }
            } else {
                // Simple success
                statusDiv.innerHTML = `
                    <div class="bg-green-50 border border-green-200 rounded p-3">
                        <div class="text-green-900 font-medium mb-2">
                            <i class="fas fa-check-circle mr-2"></i>${data.message || 'Action completed successfully'}
                        </div>
                        ${data.output ? `<pre class="mt-2 text-xs bg-green-50 p-2 rounded overflow-auto max-h-32">${data.output}</pre>` : ''}
                    </div>
                `;
                btn.innerHTML = originalText;
                btn.disabled = false;
                if (typeof showNotification === 'function') {
                    showNotification(data.message || 'Action completed successfully!', 'success');
                }
            }
        } else {
            statusDiv.innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded p-3">
                    <div class="text-red-900 font-medium mb-2">
                        <i class="fas fa-exclamation-circle mr-2"></i>${data.message || 'Action failed'}
                    </div>
                    ${data.output ? `<pre class="mt-2 text-xs bg-red-50 p-2 rounded overflow-auto max-h-32">${data.output}</pre>` : ''}
                </div>
            `;
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    })
    .catch(error => {
        statusDiv.innerHTML = `<div class="text-red-600"><i class="fas fa-exclamation-circle mr-2"></i>Error: ${error.message}</div>`;
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}

// togglePlugin is already defined at the top of the script - no need to redefine

window.updatePlugin = function(pluginId) {
    showNotification(`Updating ${pluginId}...`, 'info');

    fetch('/api/v3/plugins/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId })
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status === 'success') {
            loadInstalledPlugins(); // Refresh the list
        }
    })
    .catch(error => {
        showNotification('Error updating plugin: ' + error.message, 'error');
    });
}

window.uninstallPlugin = function(pluginId) {
    const plugin = (window.installedPlugins || installedPlugins || []).find(p => p.id === pluginId);
    const pluginName = plugin ? (plugin.name || pluginId) : pluginId;

    if (!confirm(`Are you sure you want to uninstall ${pluginName}?`)) {
        return;
    }

    showNotification(`Uninstalling ${pluginName}...`, 'info');

    fetch('/api/v3/plugins/uninstall', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId })
    })
    .then(response => response.json())
    .then(data => {
        console.log('Uninstall response:', data);
        
        // Check if operation was queued
        if (data.status === 'success' && data.data && data.data.operation_id) {
            // Operation was queued, poll for completion
            const operationId = data.data.operation_id;
            showNotification(`Uninstall queued for ${pluginName}...`, 'info');
            pollOperationStatus(operationId, pluginId, pluginName);
        } else if (data.status === 'success') {
            // Direct uninstall completed immediately
            handleUninstallSuccess(pluginId);
        } else {
            // Error response
            showNotification(data.message || 'Failed to uninstall plugin', data.status || 'error');
        }
    })
    .catch(error => {
        console.error('Error uninstalling plugin:', error);
        showNotification('Error uninstalling plugin: ' + error.message, 'error');
    });
}

function pollOperationStatus(operationId, pluginId, pluginName, maxAttempts = 60, attempt = 0) {
    if (attempt >= maxAttempts) {
        showNotification(`Uninstall operation timed out for ${pluginName}`, 'error');
        // Refresh plugin list to see actual state
        setTimeout(() => {
            loadInstalledPlugins();
        }, 1000);
        return;
    }

    fetch(`/api/v3/plugins/operation/${operationId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success' && data.data) {
                const operation = data.data;
                const status = operation.status;
                
                if (status === 'completed') {
                    // Operation completed successfully
                    handleUninstallSuccess(pluginId);
                } else if (status === 'failed') {
                    // Operation failed
                    const errorMsg = operation.error || operation.message || `Failed to uninstall ${pluginName}`;
                    showNotification(errorMsg, 'error');
                    // Refresh plugin list to see actual state
                    setTimeout(() => {
                        loadInstalledPlugins();
                    }, 1000);
                } else if (status === 'pending' || status === 'in_progress') {
                    // Still in progress, poll again
                    setTimeout(() => {
                        pollOperationStatus(operationId, pluginId, pluginName, maxAttempts, attempt + 1);
                    }, 1000); // Poll every second
                } else {
                    // Unknown status, poll again
                    setTimeout(() => {
                        pollOperationStatus(operationId, pluginId, pluginName, maxAttempts, attempt + 1);
                    }, 1000);
                }
            } else {
                // Error getting operation status, try again
                setTimeout(() => {
                    pollOperationStatus(operationId, pluginId, pluginName, maxAttempts, attempt + 1);
                }, 1000);
            }
        })
        .catch(error => {
            console.error('Error polling operation status:', error);
            // On error, refresh plugin list to see actual state
            setTimeout(() => {
                loadInstalledPlugins();
            }, 1000);
        });
}

function handleUninstallSuccess(pluginId) {
    // Remove from local array immediately for better UX
    const currentPlugins = window.installedPlugins || installedPlugins || [];
    const updatedPlugins = currentPlugins.filter(p => p.id !== pluginId);
    // Only update if list actually changed (setter will check, but we know it changed here)
    window.installedPlugins = updatedPlugins;
    if (typeof installedPlugins !== 'undefined') {
        installedPlugins = updatedPlugins;
    }
    renderInstalledPlugins(updatedPlugins);
    showNotification(`Plugin uninstalled successfully`, 'success');

    // Also refresh from server to ensure consistency
    setTimeout(() => {
        loadInstalledPlugins();
    }, 1000);
}

function refreshPlugins() {
    // Clear cache to force fresh data
    pluginStoreCache = null;
    cacheTimestamp = null;
    
    loadInstalledPlugins();
    // Fetch latest metadata from GitHub when refreshing
    searchPluginStore(true);
    showNotification('Plugins refreshed with latest metadata from GitHub', 'success');
}

function restartDisplay() {
    showNotification('Restarting display service...', 'info');
    
    fetch('/api/v3/system/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'restart_display_service' })
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
    })
    .catch(error => {
        showNotification('Error restarting display: ' + error.message, 'error');
    });
}

function searchPluginStore(fetchCommitInfo = true) {
    console.log('Searching plugin store...', { fetchCommitInfo });
    const query = document.getElementById('plugin-search').value;
    const category = document.getElementById('plugin-category').value;

    // For filtered searches (user typing), we can use cache to avoid excessive API calls
    // For initial load or refresh, always fetch fresh metadata
    const isFilteredSearch = query || category;
    const now = Date.now();
    const isCacheValid = pluginStoreCache && cacheTimestamp && (now - cacheTimestamp < CACHE_DURATION);
    
    // Only use cache for filtered searches that don't explicitly request fresh metadata
    if (isFilteredSearch && isCacheValid && !fetchCommitInfo) {
        console.log('Using cached plugin store data for filtered search');
        // Ensure plugin store grid exists before rendering
        const storeGrid = document.getElementById('plugin-store-grid');
        if (!storeGrid) {
            console.error('plugin-store-grid element not found, cannot render cached plugins');
            // Don't return, let it fetch fresh data
        } else {
            renderPluginStore(pluginStoreCache);
            try {
                const countEl = document.getElementById('store-count');
                if (countEl) {
                    countEl.innerHTML = `${pluginStoreCache.length} available`;
                }
            } catch (e) {
                console.warn('Could not update store count:', e);
            }
            return;
        }
    }

    // Show loading state - safely check element exists
    try {
        const countEl = document.getElementById('store-count');
        if (countEl) {
            countEl.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Loading...';
        }
    } catch (e) {
        console.warn('Could not update store count:', e);
    }
    showStoreLoading(true);

    let url = '/api/v3/plugins/store/list';
    const params = new URLSearchParams();
    if (query) params.append('query', query);
    if (category) params.append('category', category);
    // Always fetch fresh commit metadata unless explicitly disabled (for performance on repeated filtered searches)
    if (!fetchCommitInfo) {
        params.append('fetch_commit_info', 'false');
    }
    // Note: fetch_commit_info defaults to true on the server side to keep metadata fresh

    if (params.toString()) {
        url += '?' + params.toString();
    }

    console.log('Store URL:', url);

    fetch(url)
        .then(response => {
            console.log('Store response:', response.status);
            return response.json();
        })
        .then(data => {
            console.log('Store data:', data);
            showStoreLoading(false);
            
            if (data.status === 'success') {
                const plugins = data.data.plugins || [];
                console.log('Store plugins count:', plugins.length);
                
                // Cache the results if no filters
                if (!query && !category) {
                    pluginStoreCache = plugins;
                    cacheTimestamp = Date.now();
                    console.log('Cached plugin store data');
                }
                
                // Ensure plugin store grid exists before rendering
                const storeGrid = document.getElementById('plugin-store-grid');
                if (!storeGrid) {
                    console.error('plugin-store-grid element not found, cannot render plugins');
                    showError('Plugin store container not found. Please refresh the page.');
                    return;
                }
                
                renderPluginStore(plugins);

                // Update count - safely check element exists
                try {
                    const countEl = document.getElementById('store-count');
                    if (countEl) {
                        countEl.innerHTML = `${plugins.length} available`;
                    }
                } catch (e) {
                    console.warn('Could not update store count:', e);
                }
            } else {
                showError('Failed to search plugin store: ' + data.message);
                try {
                    const countEl = document.getElementById('store-count');
                    if (countEl) {
                        countEl.innerHTML = 'Error loading';
                    }
                } catch (e) {
                    console.warn('Could not update store count:', e);
                }
            }
        })
        .catch(error => {
            console.error('Error searching plugin store:', error);
            showStoreLoading(false);
            let errorMsg = 'Error searching plugin store: ' + error.message;
            if (error.message && error.message.includes('Failed to Fetch')) {
                errorMsg += ' - Please try refreshing your browser.';
            }
            showError(errorMsg);
            try {
                const countEl = document.getElementById('store-count');
                if (countEl) {
                    countEl.innerHTML = 'Error loading';
                }
            } catch (e) {
                console.warn('Could not update store count:', e);
            }
        });
}

function showStoreLoading(show) {
    const loading = document.querySelector('.store-loading');
    if (loading) {
        loading.style.display = show ? 'block' : 'none';
    }
}

function renderPluginStore(plugins) {
    const container = document.getElementById('plugin-store-grid');
    if (!container) {
        console.warn('[RENDER] plugin-store-grid not yet available, deferring render until plugin tab loads');
        window.__pendingStorePlugins = plugins;
        return;
    }
    
    if (!container) {
        console.error('plugin-store-grid element not found');
        showError('Plugin store container not found. Please refresh the page.');
        return;
    }

    if (plugins.length === 0) {
        container.innerHTML = `
            <div class="col-span-full empty-state">
                <div class="empty-state-icon">
                    <i class="fas fa-store"></i>
                </div>
                <p class="text-lg font-medium text-gray-700 mb-1">No plugins found</p>
                <p class="text-sm text-gray-500">Try adjusting your search criteria</p>
            </div>
        `;
        return;
    }

    // Helper function to escape for JavaScript strings
    const escapeJs = (text) => {
        return JSON.stringify(text || '');
    };

    container.innerHTML = plugins.map(plugin => `
        <div class="plugin-card">
            <div class="flex items-start justify-between mb-4">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center flex-wrap gap-2 mb-2">
                        <h4 class="font-semibold text-gray-900 text-base">${escapeHtml(plugin.name || plugin.id)}</h4>
                        ${plugin.verified ? '<span class="badge badge-success"><i class="fas fa-check-circle mr-1"></i>Verified</span>' : ''}
                        ${isNewPlugin(plugin.last_updated) ? '<span class="badge badge-info"><i class="fas fa-sparkles mr-1"></i>New</span>' : ''}
                        ${plugin._source === 'custom_repository' ? `<span class="badge badge-accent" title="From: ${escapeHtml(plugin._repository_name || plugin._repository_url || 'Custom Repository')}"><i class="fas fa-bookmark mr-1"></i>Custom</span>` : ''}
                    </div>
                    <div class="text-sm text-gray-600 space-y-1.5 mb-3">
                        <p class="flex items-center"><i class="fas fa-user mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.author || 'Unknown')}</p>
                        <p class="flex items-center"><i class="fas fa-code-branch mr-2 text-gray-400 w-4"></i>${formatCommit(plugin.last_commit, plugin.branch)}</p>
                        <p class="flex items-center"><i class="fas fa-calendar mr-2 text-gray-400 w-4"></i>${formatDate(plugin.last_updated)}</p>
                        <p class="flex items-center"><i class="fas fa-tag mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.category || 'General')}</p>
                        <p class="flex items-center"><i class="fas fa-star mr-2 text-gray-400 w-4"></i>${plugin.stars || 0} stars</p>
                    </div>
                    <p class="text-sm text-gray-700 leading-relaxed">${escapeHtml(plugin.description || 'No description available')}</p>
                </div>
            </div>

            <!-- Plugin Tags -->
            ${plugin.tags && plugin.tags.length > 0 ? `
                <div class="flex flex-wrap gap-1.5 mb-4">
                    ${plugin.tags.map(tag => `<span class="badge badge-info">${escapeHtml(tag)}</span>`).join('')}
                </div>
            ` : ''}

            <!-- Store Actions -->
            <div class="mt-4 pt-4 border-t border-gray-200 space-y-2">
                <div class="flex items-center gap-2">
                    <label for="branch-input-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}" class="text-xs text-gray-600 whitespace-nowrap">
                        <i class="fas fa-code-branch mr-1"></i>Branch:
                    </label>
                    <input type="text" id="branch-input-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}" 
                           placeholder="main (default)" 
                           class="flex-1 px-2 py-1 text-xs border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                <div class="flex gap-2">
                    <button onclick='if(window.installPlugin){const branchInput = document.getElementById("branch-input-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}"); window.installPlugin(${escapeJs(plugin.id)}, branchInput?.value?.trim() || null)}else{console.error("installPlugin not available")}' class="btn bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold">
                        <i class="fas fa-download mr-2"></i>Install
                    </button>
                    <button onclick='window.open(${escapeJs(plugin.repo || '#')}, "_blank")' class="btn bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold">
                        <i class="fas fa-external-link-alt mr-2"></i>View
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

// Expose functions to window for onclick handlers
window.installPlugin = function(pluginId, branch = null) {
    showNotification(`Installing ${pluginId}${branch ? ` (branch: ${branch})` : ''}...`, 'info');

    const requestBody = { plugin_id: pluginId };
    if (branch) {
        requestBody.branch = branch;
    }

    fetch('/api/v3/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status === 'success') {
            // Refresh both installed plugins and store
            loadInstalledPlugins();
            // Delay store refresh slightly to ensure DOM is ready
            setTimeout(() => {
                searchPluginStore();
            }, 100);
        }
    })
    .catch(error => {
        showNotification('Error installing plugin: ' + error.message, 'error');
    });
}

function setupCollapsibleSections() {
    // Toggle Installed Plugins section
    const toggleInstalledBtn = document.getElementById('toggle-installed-plugins');
    const installedContent = document.getElementById('installed-plugins-content');
    const installedIcon = document.getElementById('installed-plugins-icon');
    
    if (toggleInstalledBtn && installedContent) {
        toggleInstalledBtn.addEventListener('click', function() {
            const isHidden = installedContent.style.display === 'none' || installedContent.classList.contains('hidden');
            if (isHidden) {
                installedContent.style.display = 'block';
                installedContent.classList.remove('hidden');
                installedIcon.classList.remove('fa-chevron-down');
                installedIcon.classList.add('fa-chevron-up');
                toggleInstalledBtn.querySelector('span').textContent = 'Collapse';
            } else {
                installedContent.style.display = 'none';
                installedContent.classList.add('hidden');
                installedIcon.classList.remove('fa-chevron-up');
                installedIcon.classList.add('fa-chevron-down');
                toggleInstalledBtn.querySelector('span').textContent = 'Expand';
            }
        });
    }
    
    // Toggle Plugin Store section
    const toggleStoreBtn = document.getElementById('toggle-plugin-store');
    const storeContent = document.getElementById('plugin-store-content');
    const storeIcon = document.getElementById('plugin-store-icon');
    
    if (toggleStoreBtn && storeContent) {
        toggleStoreBtn.addEventListener('click', function() {
            const isHidden = storeContent.style.display === 'none' || storeContent.classList.contains('hidden');
            if (isHidden) {
                storeContent.style.display = 'block';
                storeContent.classList.remove('hidden');
                storeIcon.classList.remove('fa-chevron-down');
                storeIcon.classList.add('fa-chevron-up');
                toggleStoreBtn.querySelector('span').textContent = 'Collapse';
            } else {
                storeContent.style.display = 'none';
                storeContent.classList.add('hidden');
                storeIcon.classList.remove('fa-chevron-up');
                storeIcon.classList.add('fa-chevron-down');
                toggleStoreBtn.querySelector('span').textContent = 'Expand';
            }
        });
    }
    
    // Toggle GitHub Token Settings section
    const toggleTokenCollapseBtn = document.getElementById('toggle-github-token-collapse');
    const tokenContent = document.getElementById('github-token-content');
    const tokenIconCollapse = document.getElementById('github-token-icon-collapse');
    
    if (toggleTokenCollapseBtn && tokenContent) {
        toggleTokenCollapseBtn.addEventListener('click', function(e) {
            e.stopPropagation(); // Prevent triggering the close button
            const isHidden = tokenContent.style.display === 'none' || tokenContent.classList.contains('hidden');
            if (isHidden) {
                tokenContent.style.display = 'block';
                tokenContent.classList.remove('hidden');
                tokenIconCollapse.classList.remove('fa-chevron-down');
                tokenIconCollapse.classList.add('fa-chevron-up');
                toggleTokenCollapseBtn.querySelector('span').textContent = 'Collapse';
            } else {
                tokenContent.style.display = 'none';
                tokenContent.classList.add('hidden');
                tokenIconCollapse.classList.remove('fa-chevron-up');
                tokenIconCollapse.classList.add('fa-chevron-down');
                toggleTokenCollapseBtn.querySelector('span').textContent = 'Expand';
            }
        });
    }
}

function loadSavedRepositories() {
    fetch('/api/v3/plugins/saved-repositories')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                renderSavedRepositories(data.data.repositories || []);
            }
        })
        .catch(error => {
            console.error('Error loading saved repositories:', error);
        });
}

function renderSavedRepositories(repositories) {
    const container = document.getElementById('saved-repositories-list');
    const countEl = document.getElementById('saved-repos-count');
    
    if (!container) return;
    
    if (countEl) {
        countEl.textContent = `${repositories.length} saved`;
    }
    
    if (repositories.length === 0) {
        container.innerHTML = '<p class="text-xs text-gray-500 italic">No saved repositories yet. Save a repository URL to see it here.</p>';
        return;
    }
    
    // Helper function to escape for JavaScript strings
    const escapeJs = (text) => {
        return JSON.stringify(text || '');
    };
    
    container.innerHTML = repositories.map(repo => {
        const repoUrl = repo.url || '';
        const repoName = repo.name || repoUrl;
        const repoType = repo.type || 'single';
        
        return `
            <div class="bg-white border border-gray-200 rounded p-2 flex items-center justify-between">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                        <i class="fas ${repoType === 'registry' ? 'fa-folder-open' : 'fa-code-branch'} text-gray-400 text-xs"></i>
                        <span class="text-sm font-medium text-gray-900 truncate" title="${repoUrl}">${escapeHtml(repoName)}</span>
                    </div>
                    <p class="text-xs text-gray-500 truncate" title="${repoUrl}">${escapeHtml(repoUrl)}</p>
                </div>
                <button onclick='if(window.removeSavedRepository){window.removeSavedRepository(${escapeJs(repoUrl)})}else{console.error("removeSavedRepository not available")}' class="ml-2 text-red-600 hover:text-red-800 text-xs px-2 py-1" title="Remove repository">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `;
    }).join('');
}

window.removeSavedRepository = function(repoUrl) {
    if (!confirm('Remove this saved repository? Its plugins will no longer appear in the store.')) {
        return;
    }
    
    fetch('/api/v3/plugins/saved-repositories', {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ repo_url: repoUrl })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showSuccess('Repository removed successfully');
            renderSavedRepositories(data.data.repositories || []);
            // Refresh plugin store to remove plugins from deleted repo
            searchPluginStore();
        } else {
            showError(data.message || 'Failed to remove repository');
        }
    })
    .catch(error => {
        showError('Error removing repository: ' + error.message);
    });
}

function setupGitHubInstallHandlers() {
    // Toggle GitHub install section visibility
    const toggleBtn = document.getElementById('toggle-github-install');
    const installSection = document.getElementById('github-install-section');
    const icon = document.getElementById('github-install-icon');
    
    if (toggleBtn && installSection) {
        toggleBtn.addEventListener('click', function() {
            const isHidden = installSection.classList.contains('hidden');
            if (isHidden) {
                installSection.classList.remove('hidden');
                icon.classList.remove('fa-chevron-down');
                icon.classList.add('fa-chevron-up');
                toggleBtn.querySelector('span').textContent = 'Hide';
            } else {
                installSection.classList.add('hidden');
                icon.classList.remove('fa-chevron-up');
                icon.classList.add('fa-chevron-down');
                toggleBtn.querySelector('span').textContent = 'Show';
            }
        });
    }
    
    // Install single plugin from URL
    const installBtn = document.getElementById('install-plugin-from-url');
    const pluginUrlInput = document.getElementById('github-plugin-url');
    const pluginStatusDiv = document.getElementById('github-plugin-status');
    
    if (installBtn && pluginUrlInput) {
        installBtn.addEventListener('click', function() {
            const repoUrl = pluginUrlInput.value.trim();
            if (!repoUrl) {
                pluginStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a GitHub URL</span>';
                return;
            }
            
            if (!repoUrl.includes('github.com')) {
                pluginStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a valid GitHub URL</span>';
                return;
            }
            
            installBtn.disabled = true;
            installBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Installing...';
            pluginStatusDiv.innerHTML = '<span class="text-blue-600"><i class="fas fa-spinner fa-spin mr-1"></i>Installing plugin...</span>';
            
            const branch = document.getElementById('plugin-branch-input')?.value?.trim() || null;
            const requestBody = { repo_url: repoUrl };
            if (branch) {
                requestBody.branch = branch;
            }
            
            fetch('/api/v3/plugins/install-from-url', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    pluginStatusDiv.innerHTML = `<span class="text-green-600"><i class="fas fa-check-circle mr-1"></i>Successfully installed: ${data.plugin_id}</span>`;
                    pluginUrlInput.value = '';
                    
                    // Refresh installed plugins list
                    setTimeout(() => {
                        loadInstalledPlugins();
                    }, 1000);
                } else {
                    pluginStatusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>${data.message || 'Installation failed'}</span>`;
                }
            })
            .catch(error => {
                pluginStatusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>Error: ${error.message}</span>`;
            })
            .finally(() => {
                installBtn.disabled = false;
                installBtn.innerHTML = '<i class="fas fa-download mr-2"></i>Install';
            });
        });
        
        // Allow Enter key to trigger install
        pluginUrlInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                installBtn.click();
            }
        });
    }
    
    // Load registry from URL
    const loadRegistryBtn = document.getElementById('load-registry-from-url');
    const registryUrlInput = document.getElementById('github-registry-url');
    const registryStatusDiv = document.getElementById('registry-status');
    const customRegistryPlugins = document.getElementById('custom-registry-plugins');
    const customRegistryGrid = document.getElementById('custom-registry-grid');
    
    if (loadRegistryBtn && registryUrlInput) {
        loadRegistryBtn.addEventListener('click', function() {
            const repoUrl = registryUrlInput.value.trim();
            if (!repoUrl) {
                registryStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a GitHub URL</span>';
                return;
            }
            
            if (!repoUrl.includes('github.com')) {
                registryStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a valid GitHub URL</span>';
                return;
            }
            
            loadRegistryBtn.disabled = true;
            loadRegistryBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Loading...';
            registryStatusDiv.innerHTML = '<span class="text-blue-600"><i class="fas fa-spinner fa-spin mr-1"></i>Loading registry...</span>';
            customRegistryPlugins.classList.add('hidden');
            
            fetch('/api/v3/plugins/registry-from-url', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ repo_url: repoUrl })
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success' && data.plugins && data.plugins.length > 0) {
                    registryStatusDiv.innerHTML = `<span class="text-green-600"><i class="fas fa-check-circle mr-1"></i>Found ${data.plugins.length} plugins</span>`;
                    renderCustomRegistryPlugins(data.plugins, repoUrl);
                    customRegistryPlugins.classList.remove('hidden');
                } else {
                    registryStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>No valid registry found or registry is empty</span>';
                    customRegistryPlugins.classList.add('hidden');
                }
            })
            .catch(error => {
                registryStatusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>Error: ${error.message}</span>`;
                customRegistryPlugins.classList.add('hidden');
            })
            .finally(() => {
                loadRegistryBtn.disabled = false;
                loadRegistryBtn.innerHTML = '<i class="fas fa-search mr-2"></i>Load Registry';
            });
        });
        
        // Allow Enter key to trigger load
        registryUrlInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                loadRegistryBtn.click();
            }
        });
    }
    
    // Save registry URL button
    const saveRegistryBtn = document.getElementById('save-registry-url');
    if (saveRegistryBtn && registryUrlInput) {
        saveRegistryBtn.addEventListener('click', function() {
            const repoUrl = registryUrlInput.value.trim();
            if (!repoUrl) {
                showError('Please enter a repository URL first');
                return;
            }
            
            if (!repoUrl.includes('github.com')) {
                showError('Please enter a valid GitHub URL');
                return;
            }
            
            saveRegistryBtn.disabled = true;
            saveRegistryBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Saving...';
            
            fetch('/api/v3/plugins/saved-repositories', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ repo_url: repoUrl })
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    showSuccess('Repository saved successfully! Its plugins will appear in the Plugin Store.');
                    renderSavedRepositories(data.data.repositories || []);
                    // Refresh plugin store to include new repo
                    searchPluginStore();
                } else {
                    showError(data.message || 'Failed to save repository');
                }
            })
            .catch(error => {
                showError('Error saving repository: ' + error.message);
            })
            .finally(() => {
                saveRegistryBtn.disabled = false;
                saveRegistryBtn.innerHTML = '<i class="fas fa-bookmark mr-2"></i>Save Repository';
            });
        });
    }
    
    // Refresh saved repos button
    const refreshSavedReposBtn = document.getElementById('refresh-saved-repos');
    if (refreshSavedReposBtn) {
        refreshSavedReposBtn.addEventListener('click', function() {
            loadSavedRepositories();
            searchPluginStore(); // Also refresh plugin store
            showSuccess('Repositories refreshed');
        });
    }
}

function renderCustomRegistryPlugins(plugins, registryUrl) {
    const container = document.getElementById('custom-registry-grid');
    if (!container) return;
    
    if (plugins.length === 0) {
        container.innerHTML = '<p class="text-sm text-gray-500 col-span-full">No plugins found in this registry</p>';
        return;
    }
    
    // Escape HTML helper
    const escapeHtml = (text) => {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    };
    
    // Helper function to escape for JavaScript strings
    const escapeJs = (text) => {
        return JSON.stringify(text || '');
    };
    
    container.innerHTML = plugins.map(plugin => {
        const isInstalled = installedPlugins.some(p => p.id === plugin.id);
        const pluginIdJs = escapeJs(plugin.id);
        const escapedUrlJs = escapeJs(registryUrl);
        const pluginPathJs = escapeJs(plugin.plugin_path || '');
        const branchInputId = `branch-input-custom-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}`;
        
        const installBtn = isInstalled 
            ? '<button class="px-3 py-1 text-xs bg-gray-400 text-white rounded cursor-not-allowed" disabled><i class="fas fa-check mr-1"></i>Installed</button>'
            : `<button onclick='if(window.installFromCustomRegistry){const branchInput = document.getElementById("${branchInputId}"); window.installFromCustomRegistry(${pluginIdJs}, ${escapedUrlJs}, ${pluginPathJs}, branchInput?.value?.trim() || null)}else{console.error("installFromCustomRegistry not available")}' class="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded"><i class="fas fa-download mr-1"></i>Install</button>`;
        
        return `
            <div class="bg-white border border-gray-200 rounded-lg p-3">
                <div class="flex items-start justify-between mb-2">
                    <div class="flex-1">
                        <h5 class="font-semibold text-sm text-gray-900">${escapeHtml(plugin.name || plugin.id)}</h5>
                        <p class="text-xs text-gray-600 mt-1 line-clamp-2">${escapeHtml(plugin.description || 'No description')}</p>
                    </div>
                </div>
                <div class="space-y-2 mt-2 pt-2 border-t border-gray-100">
                    <div class="flex items-center gap-2">
                        <label for="${branchInputId}" class="text-xs text-gray-600 whitespace-nowrap">
                            <i class="fas fa-code-branch mr-1"></i>Branch:
                        </label>
                        <input type="text" id="${branchInputId}" 
                               placeholder="main (default)" 
                               class="flex-1 px-2 py-1 text-xs border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">Last updated ${formatDate(plugin.last_updated)}</span>
                        ${installBtn}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

window.installFromCustomRegistry = function(pluginId, registryUrl, pluginPath, branch = null) {
    const repoUrl = registryUrl;
    const requestBody = { 
        repo_url: repoUrl,
        plugin_id: pluginId,
        plugin_path: pluginPath
    };
    if (branch) {
        requestBody.branch = branch;
    }
    
    fetch('/api/v3/plugins/install-from-url', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(requestBody)
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showSuccess(`Plugin ${data.plugin_id} installed successfully`);
            // Refresh installed plugins and re-render custom registry
            loadInstalledPlugins();
            // Re-render custom registry to update install buttons
            const registryUrlInput = document.getElementById('github-registry-url');
            if (registryUrlInput && registryUrlInput.value.trim()) {
                document.getElementById('load-registry-from-url').click();
            }
        } else {
            showError(data.message || 'Installation failed');
        }
    })
    .catch(error => {
        let errorMsg = 'Error installing plugin: ' + error.message;
        if (error.message && error.message.includes('Failed to Fetch')) {
            errorMsg += ' - Please try refreshing your browser.';
        }
        showError(errorMsg);
    });
}

function showSuccess(message) {
    // Try to use notification system if available, otherwise use alert
    if (typeof showNotification === 'function') {
        showNotification(message, 'success');
    } else {
        console.log('Success: ' + message);
        // Show a temporary success message
        const statusDiv = document.getElementById('github-plugin-status') || document.getElementById('registry-status');
        if (statusDiv) {
            statusDiv.innerHTML = `<span class="text-green-600"><i class="fas fa-check-circle mr-1"></i>${message}</span>`;
            setTimeout(() => {
                if (statusDiv) statusDiv.innerHTML = '';
            }, 5000);
        }
    }
}

function showError(message) {
    const content = document.getElementById('plugins-content');
    if (!content) {
        console.error('plugins-content element not found');
        if (typeof showNotification === 'function') {
            showNotification(message, 'error');
        } else {
            console.error('Error: ' + message);
        }
        return;
    }
    content.innerHTML = `
        <div class="text-center py-8">
            <i class="fas fa-exclamation-triangle text-4xl text-red-400 mb-2"></i>
            <p class="text-red-600">${escapeHtml(message)}</p>
        </div>
    `;
}

// Plugin configuration form submission
document.addEventListener('submit', function(e) {
    if (e.target.id === 'plugin-config-form') {
        e.preventDefault();

        const formData = new FormData(e.target);
        const config = {};
        const schema = currentPluginConfig?.schema;

        // Convert form data to config object
        // Note: 'enabled' is managed separately via the header toggle, not through this form
        for (let [key, value] of formData.entries()) {
            // Skip enabled - it's managed separately via the header toggle
            if (key === 'enabled') continue;

            // Check if this field is an array type in the schema
            if (schema?.properties?.[key]?.type === 'array') {
                // Convert comma-separated string to array
                const arrayValue = value.split(',').map(item => item.trim()).filter(item => item.length > 0);
                config[key] = arrayValue;
                console.log(`Array field ${key}: "${value}" -> `, arrayValue);
            } else if (key === 'display_duration' || schema?.properties?.[key]?.type === 'integer') {
                config[key] = parseInt(value);
            } else if (schema?.properties?.[key]?.type === 'number') {
                config[key] = parseFloat(value);
            } else if (schema?.properties?.[key]?.type === 'boolean') {
                config[key] = value === 'true' || value === true;
            } else {
                config[key] = value;
            }
        }

        console.log('Final config to save:', config);
        console.log('Schema loaded:', schema ? 'Yes' : 'No');

        // Save the configuration
        savePluginConfiguration(currentPluginConfig.pluginId, config);
    }
});

function savePluginConfiguration(pluginId, config) {
    // Update the plugin configuration in the backend
    fetch('/api/v3/plugins/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plugin_id: pluginId, config })
    })
    .then(response => {
        if (!response.ok) {
            // Try to parse error response
            return response.json().then(data => {
                // Return error data with status
                return { error: true, status: response.status, ...data };
            }).catch(() => {
                // If JSON parsing fails, return generic error
                return { 
                    error: true, 
                    status: response.status, 
                    message: `Server error: ${response.status} ${response.statusText}` 
                };
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.error || data.status !== 'success') {
            // Display validation errors if present
            if (data.validation_errors && Array.isArray(data.validation_errors)) {
                displayValidationErrors(data.validation_errors);
            }
            let errorMessage = data.message || 'Error saving configuration';
            if (data.validation_errors && Array.isArray(data.validation_errors) && data.validation_errors.length > 0) {
                errorMessage += '\n\nValidation errors:\n' + data.validation_errors.join('\n');
            }
            showNotification(errorMessage, 'error');
            console.error('Config save failed:', data);
        } else {
            // Hide validation errors on success
            displayValidationErrors([]);
            showNotification(data.message || 'Configuration saved successfully', data.status);
            closePluginConfigModal();
            // Refresh the installed plugins to update the UI
            loadInstalledPlugins();
        }
    })
    .catch(error => {
        console.error('Error saving plugin config:', error);
        showNotification('Error saving plugin configuration: ' + error.message, 'error');
    });
}

// Utility function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Format date for display
function formatDate(dateString) {
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

function formatCommit(commit, branch) {
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

// Check if plugin is new (updated within last 7 days)
function isNewPlugin(lastUpdated) {
    if (!lastUpdated) return false;
    
    try {
        const date = new Date(lastUpdated);
        const now = new Date();
        const diffTime = Math.abs(now - date);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        
        return diffDays <= 7;
    } catch (e) {
        return false;
    }
}

// Debounce utility
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Toggle password visibility for secret fields
function togglePasswordVisibility(fieldId) {
    const input = document.getElementById(fieldId);
    const icon = document.getElementById(fieldId + '-icon');
    
    if (input && icon) {
        if (input.type === 'password') {
            input.type = 'text';
            icon.classList.remove('fa-eye');
            icon.classList.add('fa-eye-slash');
        } else {
            input.type = 'password';
            icon.classList.remove('fa-eye-slash');
            icon.classList.add('fa-eye');
        }
    }
}

// GitHub Token Configuration Functions
window.toggleGithubTokenSettings = function() {
    const settings = document.getElementById('github-token-settings');
    if (settings) {
        settings.classList.toggle('hidden');
        if (!settings.classList.contains('hidden')) {
            loadGithubToken();
        }
    }
}

window.toggleGithubTokenVisibility = function() {
    const input = document.getElementById('github-token-input');
    const icon = document.getElementById('github-token-icon');
    
    if (input && icon) {
        if (input.type === 'password') {
            input.type = 'text';
            icon.classList.remove('fa-eye');
            icon.classList.add('fa-eye-slash');
        } else {
            input.type = 'password';
            icon.classList.remove('fa-eye-slash');
            icon.classList.add('fa-eye');
        }
    }
}

window.loadGithubToken = function() {
    fetch('/api/v3/config/secrets')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const token = data.data?.github?.api_token || '';
                const input = document.getElementById('github-token-input');
                if (input) {
                    input.value = token;
                    if (token && token !== 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN') {
                        showNotification('GitHub token loaded successfully', 'success');
                    } else {
                        showNotification('No GitHub token configured', 'info');
                    }
                }
            }
        })
        .catch(error => {
            console.error('Error loading GitHub token:', error);
            showNotification('Error loading GitHub token', 'error');
        });
}

window.saveGithubToken = function() {
    const input = document.getElementById('github-token-input');
    if (!input) return;
    
    const token = input.value.trim();
    
    if (!token) {
        showNotification('Please enter a GitHub token', 'error');
        return;
    }
    
    // Load current secrets config
    fetch('/api/v3/config/secrets')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const secrets = data.data || {};
                
                // Update GitHub token
                if (!secrets.github) {
                    secrets.github = {};
                }
                secrets.github.api_token = token;
                
                // Save updated secrets
                return fetch('/api/v3/config/raw/secrets', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(secrets)
                });
            } else {
                throw new Error('Failed to load current secrets');
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                showNotification('GitHub token saved successfully! Rate limit increased to 5,000/hour', 'success');
                // Hide the warning banner
                const warning = document.getElementById('github-auth-warning');
                if (warning) {
                    warning.classList.add('hidden');
                }
                // Hide the settings panel after successful save
                setTimeout(() => {
                    toggleGithubTokenSettings();
                }, 2000);
            } else {
                showNotification('Error saving GitHub token: ' + data.message, 'error');
            }
        })
        .catch(error => {
            console.error('Error saving GitHub token:', error);
            showNotification('Error saving GitHub token: ' + error.message, 'error');
        });
}

// GitHub Authentication Status
// Only shows the warning banner if no GitHub token is configured
// The token itself is never exposed to the frontend for security
function checkGitHubAuthStatus() {
    fetch('/api/v3/plugins/store/github-status')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const authData = data.data;

                // Only show the banner if no GitHub token is configured
                if (!authData.authenticated) {
                    // Check if user has dismissed the warning (stored in session storage)
                    const dismissed = sessionStorage.getItem('github-auth-warning-dismissed');
                    if (!dismissed) {
                        const warning = document.getElementById('github-auth-warning');
                        const rateLimit = document.getElementById('rate-limit-count');

                        if (warning && rateLimit) {
                            rateLimit.textContent = authData.rate_limit;
                            warning.classList.remove('hidden');
                            console.log('GitHub token not configured - showing API limit warning');
                        }
                    }
                } else {
                    // Token is configured - ensure the banner is hidden
                    const warning = document.getElementById('github-auth-warning');
                    if (warning) {
                        warning.classList.add('hidden');
                        console.log('GitHub token is configured - API limit warning hidden');
                    }
                    
                    // Also hide the GitHub token settings panel if it's visible
                    const settings = document.getElementById('github-token-settings');
                    if (settings && !settings.classList.contains('hidden')) {
                        settings.classList.add('hidden');
                        console.log('GitHub token is configured - hiding settings panel');
                    }
                }
            }
        })
        .catch(error => {
            console.error('Error checking GitHub auth status:', error);
        });
}

window.dismissGithubWarning = function() {
    const warning = document.getElementById('github-auth-warning');
    if (warning) {
        warning.classList.add('hidden');
        // Remember dismissal for this session
        sessionStorage.setItem('github-auth-warning-dismissed', 'true');
    }
}

window.showGithubTokenInstructions = function() {
    const instructions = `
        <div class="space-y-4">
            <h4 class="font-semibold text-lg">How to Add a GitHub Token</h4>
            
            <div class="space-y-3">
                <div class="bg-gray-50 p-3 rounded">
                    <h5 class="font-medium mb-2">Step 1: Create a GitHub Token</h5>
                    <ol class="list-decimal list-inside space-y-1 text-sm">
                        <li>Click the "Create a GitHub Token" link above (or <a href="https://github.com/settings/tokens/new?description=LEDMatrix%20Plugin%20Manager&scopes=" target="_blank" class="text-blue-600 underline">click here</a>)</li>
                        <li>Give it a name like "LEDMatrix Plugin Manager"</li>
                        <li>No special scopes/permissions are needed for public repositories</li>
                        <li>Click "Generate token" at the bottom</li>
                        <li>Copy the generated token (it starts with "ghp_")</li>
                    </ol>
                </div>
                
                <div class="bg-gray-50 p-3 rounded">
                    <h5 class="font-medium mb-2">Step 2: Add Token to LEDMatrix</h5>
                    <ol class="list-decimal list-inside space-y-1 text-sm">
                        <li>SSH into your Raspberry Pi</li>
                        <li>Edit the secrets file: <code class="bg-gray-200 px-1 rounded">nano ~/LEDMatrix/config/config_secrets.json</code></li>
                        <li>Find the "github" section and add your token:
                            <pre class="bg-gray-800 text-white p-2 rounded mt-2 text-xs overflow-x-auto">"github": {
  "api_token": "ghp_your_token_here"
}</pre>
                        </li>
                        <li>Save the file (Ctrl+O, Enter, Ctrl+X)</li>
                        <li>Restart the web service: <code class="bg-gray-200 px-1 rounded">sudo systemctl restart ledmatrix-web</code></li>
                    </ol>
                </div>
                
                <div class="bg-blue-50 p-3 rounded border border-blue-200">
                    <p class="text-sm text-blue-800">
                        <i class="fas fa-info-circle mr-2"></i>
                        <strong>Note:</strong> Your token is stored locally and never shared. It's only used to authenticate API requests to GitHub.
                    </p>
                </div>
            </div>
            
            <div class="flex justify-end">
                <button onclick="closeInstructionsModal()" class="btn bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md">
                    Got it!
                </button>
            </div>
        </div>
    `;
    
    // Use the existing plugin config modal for instructions
    const modal = document.getElementById('plugin-config-modal');
    const title = document.getElementById('plugin-config-title');
    const content = document.getElementById('plugin-config-content');
    
    title.textContent = 'GitHub Token Setup';
    content.innerHTML = instructions;
    modal.style.display = 'flex';
    console.log('GitHub instructions modal opened');
}

window.closeInstructionsModal = function() {
    const modal = document.getElementById('plugin-config-modal');
    modal.style.display = 'none';
    console.log('Instructions modal closed');
}

// ==================== File Upload Functions ====================
// Make these globally accessible for use in base.html

window.handleFileDrop = function(event, fieldId) {
    event.preventDefault();
    const files = event.dataTransfer.files;
    if (files.length > 0) {
        window.handleFiles(fieldId, Array.from(files));
    }
}

window.handleFileSelect = function(event, fieldId) {
    const files = event.target.files;
    if (files.length > 0) {
        window.handleFiles(fieldId, Array.from(files));
    }
}

window.handleCredentialsUpload = async function(event, fieldId, uploadEndpoint, targetFilename) {
    const file = event.target.files[0];
    if (!file) {
        return;
    }
    
    // Validate file extension
    const fileExt = '.' + file.name.split('.').pop().toLowerCase();
    if (!fileExt || fileExt === '.') {
        showNotification('Please select a valid file', 'error');
        return;
    }
    
    // Validate file size (1MB max)
    if (file.size > 1024 * 1024) {
        showNotification('File exceeds 1MB limit', 'error');
        return;
    }
    
    // Show upload status
    const statusEl = document.getElementById(fieldId + '_status');
    if (statusEl) {
        statusEl.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Uploading...';
    }
    
    // Create form data
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch(uploadEndpoint, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            // Update hidden input with filename
            const hiddenInput = document.getElementById(fieldId + '_hidden');
            if (hiddenInput) {
                hiddenInput.value = targetFilename || file.name;
            }
            
            // Update status
            if (statusEl) {
                statusEl.innerHTML = `✓ Uploaded: ${targetFilename || file.name}`;
                statusEl.className = 'text-sm text-green-600';
            }
            
            showNotification('Credentials file uploaded successfully', 'success');
        } else {
            if (statusEl) {
                statusEl.innerHTML = 'Upload failed - click to try again';
                statusEl.className = 'text-sm text-gray-600';
            }
            showNotification(data.message || 'Upload failed', 'error');
        }
    } catch (error) {
        if (statusEl) {
            statusEl.innerHTML = 'Upload failed - click to try again';
            statusEl.className = 'text-sm text-gray-600';
        }
        showNotification('Error uploading file: ' + error.message, 'error');
    }
}

window.handleFiles = async function(fieldId, files) {
    const uploadConfig = window.getUploadConfig(fieldId);
    const pluginId = uploadConfig.plugin_id || window.currentPluginConfig?.pluginId || 'static-image';
    const maxFiles = uploadConfig.max_files || 10;
    const maxSizeMB = uploadConfig.max_size_mb || 5;
    
    // Validate file count
    const currentImages = window.getCurrentImages(fieldId);
    if (currentImages.length + files.length > maxFiles) {
        showNotification(`Maximum ${maxFiles} files allowed. You have ${currentImages.length} and tried to add ${files.length}.`, 'error');
        return;
    }
    
    // Validate file types and sizes
    const validFiles = [];
    for (const file of files) {
        if (file.size > maxSizeMB * 1024 * 1024) {
            showNotification(`File ${file.name} exceeds ${maxSizeMB}MB limit`, 'error');
            continue;
        }
        
        const allowedTypes = ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp', 'image/gif'];
        if (!allowedTypes.includes(file.type)) {
            showNotification(`File ${file.name} is not a valid image type`, 'error');
            continue;
        }
        
        validFiles.push(file);
    }
    
    if (validFiles.length === 0) {
        return;
    }
    
    // Show upload progress
    window.showUploadProgress(fieldId, validFiles.length);
    
    // Upload files
    const formData = new FormData();
    formData.append('plugin_id', pluginId);
    validFiles.forEach(file => formData.append('files', file));
    
    try {
        const response = await fetch('/api/v3/plugins/assets/upload', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            // Add uploaded images to current list
            const currentImages = window.getCurrentImages(fieldId);
            const newImages = [...currentImages, ...data.uploaded_files];
            window.updateImageList(fieldId, newImages);
            
            showNotification(`Successfully uploaded ${data.uploaded_files.length} image(s)`, 'success');
        } else {
            showNotification(`Upload failed: ${data.message}`, 'error');
        }
    } catch (error) {
        console.error('Upload error:', error);
        showNotification(`Upload error: ${error.message}`, 'error');
    } finally {
        window.hideUploadProgress(fieldId);
        // Clear file input
        const fileInput = document.getElementById(`${fieldId}_file_input`);
        if (fileInput) {
            fileInput.value = '';
        }
    }
}

window.deleteUploadedImage = async function(fieldId, imageId, pluginId) {
    if (!confirm('Are you sure you want to delete this image?')) {
        return;
    }
    
    try {
        const response = await fetch('/api/v3/plugins/assets/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plugin_id: pluginId, image_id: imageId })
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            // Remove from current list
            const currentImages = window.getCurrentImages(fieldId);
            const newImages = currentImages.filter(img => img.id !== imageId);
            window.updateImageList(fieldId, newImages);
            
            showNotification('Image deleted successfully', 'success');
        } else {
            showNotification(`Delete failed: ${data.message}`, 'error');
        }
    } catch (error) {
        console.error('Delete error:', error);
        showNotification(`Delete error: ${error.message}`, 'error');
    }
}

window.getUploadConfig = function(fieldId) {
    // Extract config from schema
    const schema = window.currentPluginConfig?.schema;
    if (!schema || !schema.properties) return {};
    
    // Find the property that matches this fieldId
    // FieldId is like "image_config_images" for "image_config.images"
    const key = fieldId.replace(/_/g, '.');
    const keys = key.split('.');
    let prop = schema.properties;
    
    for (const k of keys) {
        if (prop && prop[k]) {
            prop = prop[k];
            if (prop.properties && prop.type === 'object') {
                prop = prop.properties;
            } else if (prop.type === 'array' && prop['x-widget'] === 'file-upload') {
                break;
            } else {
                break;
            }
        }
    }
    
    // If we found an array with x-widget, get its config
    if (prop && prop.type === 'array' && prop['x-widget'] === 'file-upload') {
        return prop['x-upload-config'] || {};
    }
    
    // Try to find nested images array
    if (schema.properties && schema.properties.image_config && 
        schema.properties.image_config.properties && 
        schema.properties.image_config.properties.images) {
        const imagesProp = schema.properties.image_config.properties.images;
        if (imagesProp['x-widget'] === 'file-upload') {
            return imagesProp['x-upload-config'] || {};
        }
    }
    
    return {};
}

window.getCurrentImages = function(fieldId) {
    const hiddenInput = document.getElementById(`${fieldId}_images_data`);
    if (hiddenInput && hiddenInput.value) {
        try {
            return JSON.parse(hiddenInput.value);
        } catch (e) {
            console.error('Error parsing images data:', e);
        }
    }
    return [];
}

window.updateImageList = function(fieldId, images) {
    const hiddenInput = document.getElementById(`${fieldId}_images_data`);
    if (hiddenInput) {
        hiddenInput.value = JSON.stringify(images);
    }
    
    // Update the display
    const imageList = document.getElementById(`${fieldId}_image_list`);
    if (imageList) {
        const uploadConfig = window.getUploadConfig(fieldId);
        const pluginId = uploadConfig.plugin_id || window.currentPluginConfig?.pluginId || 'static-image';
        
        imageList.innerHTML = images.map((img, idx) => {
            const imgSchedule = img.schedule || {};
            const hasSchedule = imgSchedule.enabled && imgSchedule.mode && imgSchedule.mode !== 'always';
            const scheduleSummary = hasSchedule ? (window.getScheduleSummary ? window.getScheduleSummary(imgSchedule) : 'Scheduled') : 'Always shown';
            
            return `
            <div id="img_${img.id || idx}" class="bg-gray-50 p-3 rounded-lg border border-gray-200">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center space-x-3 flex-1">
                        <img src="/${img.path || ''}" 
                             alt="${img.filename || ''}" 
                             class="w-16 h-16 object-cover rounded"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                        <div style="display:none;" class="w-16 h-16 bg-gray-200 rounded flex items-center justify-center">
                            <i class="fas fa-image text-gray-400"></i>
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="text-sm font-medium text-gray-900 truncate">${img.original_filename || img.filename || 'Image'}</p>
                            <p class="text-xs text-gray-500">${window.formatFileSize ? window.formatFileSize(img.size || 0) : (Math.round((img.size || 0) / 1024) + ' KB')} • ${window.formatDate ? window.formatDate(img.uploaded_at) : (img.uploaded_at || '')}</p>
                            <p class="text-xs text-blue-600 mt-1">
                                <i class="fas fa-clock mr-1"></i>${scheduleSummary}
                            </p>
                        </div>
                    </div>
                    <div class="flex items-center space-x-2 ml-4">
                        <button type="button" 
                                onclick="window.openImageSchedule('${fieldId}', '${img.id}', ${idx})"
                                class="text-blue-600 hover:text-blue-800 p-2" 
                                title="Schedule this image">
                            <i class="fas fa-calendar-alt"></i>
                        </button>
                        <button type="button" 
                                onclick="window.deleteUploadedImage('${fieldId}', '${img.id}', '${pluginId}')"
                                class="text-red-600 hover:text-red-800 p-2"
                                title="Delete image">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
                <!-- Schedule widget will be inserted here when opened -->
                <div id="schedule_${img.id || idx}" class="hidden mt-3 pt-3 border-t border-gray-300"></div>
            </div>
            `;
        }).join('');
    }
}

window.showUploadProgress = function(fieldId, totalFiles) {
    const dropZone = document.getElementById(`${fieldId}_drop_zone`);
    if (dropZone) {
        dropZone.innerHTML = `
            <i class="fas fa-spinner fa-spin text-3xl text-blue-500 mb-2"></i>
            <p class="text-sm text-gray-600">Uploading ${totalFiles} file(s)...</p>
        `;
        dropZone.style.pointerEvents = 'none';
    }
}

window.hideUploadProgress = function(fieldId) {
    const uploadConfig = window.getUploadConfig(fieldId);
    const maxFiles = uploadConfig.max_files || 10;
    const maxSizeMB = uploadConfig.max_size_mb || 5;
    const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/bmp', 'image/gif'];
    
    const dropZone = document.getElementById(`${fieldId}_drop_zone`);
    if (dropZone) {
        dropZone.innerHTML = `
            <i class="fas fa-cloud-upload-alt text-3xl text-gray-400 mb-2"></i>
            <p class="text-sm text-gray-600">Drag and drop images here or click to browse</p>
            <p class="text-xs text-gray-500 mt-1">Max ${maxFiles} files, ${maxSizeMB}MB each (PNG, JPG, GIF, BMP)</p>
        `;
        dropZone.style.pointerEvents = 'auto';
    }
}

window.formatFileSize = function(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function formatDate(dateString) {
    if (!dateString) return 'Unknown date';
    try {
        const date = new Date(dateString);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return dateString;
    }
}

window.getScheduleSummary = function(schedule) {
    if (!schedule || !schedule.enabled || schedule.mode === 'always') {
        return 'Always shown';
    }
    
    if (schedule.mode === 'time_range') {
        return `${schedule.start_time || '08:00'} - ${schedule.end_time || '18:00'} (daily)`;
    }
    
    if (schedule.mode === 'per_day' && schedule.days) {
        const enabledDays = Object.entries(schedule.days)
            .filter(([day, config]) => config && config.enabled)
            .map(([day]) => day.charAt(0).toUpperCase() + day.slice(1, 3));
        
        if (enabledDays.length === 0) {
            return 'Never shown';
        }
        
        return enabledDays.join(', ') + ' only';
    }
    
    return 'Scheduled';
}

window.openImageSchedule = function(fieldId, imageId, imageIdx) {
    const currentImages = getCurrentImages(fieldId);
    const image = currentImages[imageIdx];
    if (!image) return;
    
    const scheduleContainer = document.getElementById(`schedule_${imageId || imageIdx}`);
    if (!scheduleContainer) return;
    
    // Toggle visibility
    const isVisible = !scheduleContainer.classList.contains('hidden');
    
    if (isVisible) {
        scheduleContainer.classList.add('hidden');
        return;
    }
    
    scheduleContainer.classList.remove('hidden');
    
    const schedule = image.schedule || { enabled: false, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
    
    scheduleContainer.innerHTML = `
        <div class="bg-white rounded-lg border border-blue-200 p-4">
            <h4 class="text-sm font-semibold text-gray-900 mb-3">
                <i class="fas fa-clock mr-2"></i>Schedule Settings
            </h4>
            
            <!-- Enable Schedule -->
            <div class="mb-4">
                <label class="flex items-center">
                    <input type="checkbox" 
                           id="schedule_enabled_${imageId}"
                           ${schedule.enabled ? 'checked' : ''}
                           onchange="window.toggleImageScheduleEnabled('${fieldId}', '${imageId}', ${imageIdx})"
                           class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                    <span class="ml-2 text-sm font-medium text-gray-700">Enable schedule for this image</span>
                </label>
                <p class="ml-6 text-xs text-gray-500 mt-1">When enabled, this image will only display during scheduled times</p>
            </div>
            
            <!-- Schedule Mode -->
            <div id="schedule_options_${imageId}" class="space-y-4" style="display: ${schedule.enabled ? 'block' : 'none'};">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Schedule Type</label>
                    <select id="schedule_mode_${imageId}"
                            onchange="window.updateImageScheduleMode('${fieldId}', '${imageId}', ${imageIdx})"
                            class="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                        <option value="always" ${schedule.mode === 'always' ? 'selected' : ''}>Always Show (No Schedule)</option>
                        <option value="time_range" ${schedule.mode === 'time_range' ? 'selected' : ''}>Same Time Every Day</option>
                        <option value="per_day" ${schedule.mode === 'per_day' ? 'selected' : ''}>Different Times Per Day</option>
                    </select>
                </div>
                
                <!-- Time Range Mode -->
                <div id="time_range_${imageId}" class="grid grid-cols-2 gap-4" style="display: ${schedule.mode === 'time_range' ? 'grid' : 'none'};">
                    <div>
                        <label class="block text-xs font-medium text-gray-700 mb-1">Start Time</label>
                        <input type="time" 
                               id="schedule_start_${imageId}"
                               value="${schedule.start_time || '08:00'}"
                               onchange="window.updateImageScheduleTime('${fieldId}', '${imageId}', ${imageIdx})"
                               class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-700 mb-1">End Time</label>
                        <input type="time" 
                               id="schedule_end_${imageId}"
                               value="${schedule.end_time || '18:00'}"
                               onchange="window.updateImageScheduleTime('${fieldId}', '${imageId}', ${imageIdx})"
                               class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md">
                    </div>
                </div>
                
                <!-- Per-Day Mode -->
                <div id="per_day_${imageId}" style="display: ${schedule.mode === 'per_day' ? 'block' : 'none'};">
                    <label class="block text-xs font-medium text-gray-700 mb-2">Day-Specific Times</label>
                    <div class="bg-gray-50 rounded p-3 space-y-2 max-h-64 overflow-y-auto">
                        ${['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].map(day => {
                            const dayConfig = (schedule.days && schedule.days[day]) || { enabled: true, start_time: '08:00', end_time: '18:00' };
                            return `
                            <div class="bg-white rounded p-2 border border-gray-200">
                                <div class="flex items-center justify-between mb-2">
                                    <label class="flex items-center">
                                        <input type="checkbox"
                                               id="day_${day}_${imageId}"
                                               ${dayConfig.enabled ? 'checked' : ''}
                                               onchange="window.updateImageScheduleDay('${fieldId}', '${imageId}', ${imageIdx}, '${day}')"
                                               class="h-3 w-3 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                                        <span class="ml-2 text-xs font-medium text-gray-700 capitalize">${day}</span>
                                    </label>
                                </div>
                                <div class="grid grid-cols-2 gap-2 ml-5" id="day_times_${day}_${imageId}" style="display: ${dayConfig.enabled ? 'grid' : 'none'};">
                                    <input type="time"
                                           id="day_${day}_start_${imageId}"
                                           value="${dayConfig.start_time || '08:00'}"
                                           onchange="updateImageScheduleDay('${fieldId}', '${imageId}', ${imageIdx}, '${day}')"
                                           class="text-xs px-2 py-1 border border-gray-300 rounded"
                                           ${!dayConfig.enabled ? 'disabled' : ''}>
                                    <input type="time"
                                           id="day_${day}_end_${imageId}"
                                           value="${dayConfig.end_time || '18:00'}"
                                           onchange="updateImageScheduleDay('${fieldId}', '${imageId}', ${imageIdx}, '${day}')"
                                           class="text-xs px-2 py-1 border border-gray-300 rounded"
                                           ${!dayConfig.enabled ? 'disabled' : ''}>
                                </div>
                            </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            </div>
        </div>
    `;
}

window.toggleImageScheduleEnabled = function(fieldId, imageId, imageIdx) {
    const currentImages = window.getCurrentImages(fieldId);
    const image = currentImages[imageIdx];
    if (!image) return;
    
    const checkbox = document.getElementById(`schedule_enabled_${imageId}`);
    const enabled = checkbox.checked;
    
    if (!image.schedule) {
        image.schedule = { enabled: false, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
    }
    
    image.schedule.enabled = enabled;
    
    const optionsDiv = document.getElementById(`schedule_options_${imageId}`);
    if (optionsDiv) {
        optionsDiv.style.display = enabled ? 'block' : 'none';
    }
    
    window.updateImageList(fieldId, currentImages);
}

window.updateImageScheduleMode = function(fieldId, imageId, imageIdx) {
    const currentImages = window.getCurrentImages(fieldId);
    const image = currentImages[imageIdx];
    if (!image) return;
    
    if (!image.schedule) {
        image.schedule = { enabled: true, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
    }
    
    const modeSelect = document.getElementById(`schedule_mode_${imageId}`);
    const mode = modeSelect.value;
    
    image.schedule.mode = mode;
    
    const timeRangeDiv = document.getElementById(`time_range_${imageId}`);
    const perDayDiv = document.getElementById(`per_day_${imageId}`);
    
    if (timeRangeDiv) timeRangeDiv.style.display = mode === 'time_range' ? 'grid' : 'none';
    if (perDayDiv) perDayDiv.style.display = mode === 'per_day' ? 'block' : 'none';
    
    window.updateImageList(fieldId, currentImages);
}

window.updateImageScheduleTime = function(fieldId, imageId, imageIdx) {
    const currentImages = window.getCurrentImages(fieldId);
    const image = currentImages[imageIdx];
    if (!image) return;
    
    if (!image.schedule) {
        image.schedule = { enabled: true, mode: 'time_range', start_time: '08:00', end_time: '18:00' };
    }
    
    const startInput = document.getElementById(`schedule_start_${imageId}`);
    const endInput = document.getElementById(`schedule_end_${imageId}`);
    
    if (startInput) image.schedule.start_time = startInput.value || '08:00';
    if (endInput) image.schedule.end_time = endInput.value || '18:00';
    
    window.updateImageList(fieldId, currentImages);
}

window.updateImageScheduleDay = function(fieldId, imageId, imageIdx, day) {
    const currentImages = window.getCurrentImages(fieldId);
    const image = currentImages[imageIdx];
    if (!image) return;
    
    if (!image.schedule) {
        image.schedule = { enabled: true, mode: 'per_day', days: {} };
    }
    
    if (!image.schedule.days) {
        image.schedule.days = {};
    }
    
    const checkbox = document.getElementById(`day_${day}_${imageId}`);
    const startInput = document.getElementById(`day_${day}_start_${imageId}`);
    const endInput = document.getElementById(`day_${day}_end_${imageId}`);
    
    const enabled = checkbox ? checkbox.checked : true;
    
    if (!image.schedule.days[day]) {
        image.schedule.days[day] = { enabled: true, start_time: '08:00', end_time: '18:00' };
    }
    
    image.schedule.days[day].enabled = enabled;
    
    if (startInput) image.schedule.days[day].start_time = startInput.value || '08:00';
    if (endInput) image.schedule.days[day].end_time = endInput.value || '18:00';
    
    const timesDiv = document.getElementById(`day_times_${day}_${imageId}`);
    if (timesDiv) {
        timesDiv.style.display = enabled ? 'grid' : 'none';
        if (startInput) startInput.disabled = !enabled;
        if (endInput) endInput.disabled = !enabled;
    }
    
    window.updateImageList(fieldId, currentImages);
}

})(); // End IIFE

// Make currentPluginConfig globally accessible (outside IIFE)
window.currentPluginConfig = null;

// Force initialization immediately when script loads (for HTMX swapped content)
console.log('Plugins script loaded, checking for elements...');

// Ensure all functions are globally available (in case IIFE didn't expose them properly)
// These should already be set inside the IIFE, but this ensures they're available
if (typeof initializePluginPageWhenReady !== 'undefined') {
    window.initializePluginPageWhenReady = initializePluginPageWhenReady;
}
if (typeof initializePlugins !== 'undefined') {
    window.initializePlugins = initializePlugins;
}
if (typeof loadInstalledPlugins !== 'undefined') {
    window.loadInstalledPlugins = loadInstalledPlugins;
}
if (typeof renderInstalledPlugins !== 'undefined') {
    window.renderInstalledPlugins = renderInstalledPlugins;
}
if (typeof searchPluginStore !== 'undefined') {
    window.searchPluginStore = searchPluginStore;
}

// Verify critical functions are available
console.log('Plugin functions available:', {
        configurePlugin: typeof window.configurePlugin,
        togglePlugin: typeof window.togglePlugin,
        showPluginConfigModal: typeof window.showPluginConfigModal,
    updatePlugin: typeof window.updatePlugin,
    uninstallPlugin: typeof window.uninstallPlugin,
    initializePlugins: typeof window.initializePlugins,
    loadInstalledPlugins: typeof window.loadInstalledPlugins,
    renderInstalledPlugins: typeof window.renderInstalledPlugins
});

setTimeout(function() {
    const installedGrid = document.getElementById('installed-plugins-grid');
    if (installedGrid) {
        console.log('Found installed-plugins-grid, forcing initialization...');
        window.pluginManager.initialized = false;
        if (typeof initializePluginPageWhenReady === 'function') {
            initializePluginPageWhenReady();
        } else if (typeof window.initPluginsPage === 'function') {
            window.initPluginsPage();
        }
    } else {
        console.log('installed-plugins-grid not found yet, will retry via event listeners');
    }
}, 200);
