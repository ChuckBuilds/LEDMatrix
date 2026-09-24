/* global debugLog */
// ─── LocalStorage Safety Wrappers ────────────────────────────────────────────
// Handles environments where localStorage is unavailable or restricted (private browsing, etc.)
const safeLocalStorage = {
    getItem(key) {
        try {
            if (typeof localStorage !== 'undefined') {
                return localStorage.getItem(key);
            }
        } catch (e) {
            console.warn(`safeLocalStorage.getItem failed for key "${key}":`, e.message);
        }
        return null;
    },
    setItem(key, value) {
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(key, value);
                return true;
            }
        } catch (e) {
            console.warn(`safeLocalStorage.setItem failed for key "${key}":`, e.message);
        }
        return false;
    },
    removeItem(key) {
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem(key);
                return true;
            }
        } catch (e) {
            console.warn(`localStorage.removeItem failed for key "${key}":`, e.message);
        }
        return false;
    }
};

// Define critical functions immediately so they're available before any HTML is rendered
// Debug logging controlled by safeLocalStorage.setItem('pluginDebug', 'true')
const _PLUGIN_DEBUG_EARLY = safeLocalStorage.getItem('pluginDebug') === 'true';
if (_PLUGIN_DEBUG_EARLY) debugLog('[PLUGINS SCRIPT] Defining configurePlugin and togglePlugin at top level...');

// Define configurePlugin early to ensure it's always available
window.configurePlugin = window.configurePlugin || async function(pluginId) {
    if (_PLUGIN_DEBUG_EARLY) debugLog('[PLUGINS STUB] configurePlugin called for', pluginId);

    // Opens the plugin's own tab, the same as clicking it in the tab row.
    const appComponent = window.getApp();
    if (appComponent) {
        // Set the active tab to the plugin ID
        appComponent.activeTab = pluginId;
        if (_PLUGIN_DEBUG_EARLY) debugLog('[PLUGINS STUB] Switched to plugin tab:', pluginId);

        // Scroll to top of page to ensure the tab is visible
        window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
        console.error('Alpine.js app instance not found');
        showNotification('Unable to switch to plugin configuration. Please refresh the page.', 'error');
    }
};

// Initialize per-plugin toggle request token map for race condition protection
if (!window._pluginToggleRequests) {
    window._pluginToggleRequests = {};
}

// Define togglePlugin early to ensure it's always available
window.togglePlugin = window.togglePlugin || function(pluginId, enabled) {
    if (_PLUGIN_DEBUG_EARLY) debugLog('[PLUGINS STUB] togglePlugin called for', pluginId, 'enabled:', enabled);

    const plugin = (window.installedPlugins || []).find(p => p.id === pluginId);
    const pluginName = plugin ? (plugin.name || pluginId) : pluginId;
    const action = enabled ? 'enabling' : 'disabling';

    // Generate unique token for this toggle request to prevent race conditions
    const requestToken = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    window._pluginToggleRequests[pluginId] = requestToken;

    // Update UI immediately for better UX
    const toggleCheckbox = document.getElementById(`toggle-${pluginId}`);
    const toggleLabel = document.getElementById(`toggle-label-${pluginId}`);
    const wrapperDiv = toggleCheckbox?.parentElement?.querySelector('.flex.items-center.gap-2');
    const toggleTrack = wrapperDiv?.querySelector('.relative.w-14');
    const toggleHandle = toggleTrack?.querySelector('.absolute');

    // Disable checkbox and add disabled class to prevent overlapping requests
    if (toggleCheckbox) {
        toggleCheckbox.checked = enabled;
        toggleCheckbox.disabled = true;
        toggleCheckbox.classList.add('opacity-50', 'cursor-not-allowed');
    }

    // Disable wrapper to provide visual feedback
    if (wrapperDiv) {
        wrapperDiv.classList.add('opacity-50', 'pointer-events-none');
    }

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

    showNotification(`${action.charAt(0).toUpperCase() + action.slice(1)} ${pluginName}...`, 'info');

    return fetch('/api/v3/plugins/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId, enabled: enabled })
    })
    .then(response => response.json())
    .then(data => {
        // Verify this response is for the latest request (prevent race conditions)
        if (window._pluginToggleRequests[pluginId] !== requestToken) {
            debugLog(`[togglePlugin] Ignoring out-of-order response for ${pluginId}`);
            return;
        }

        showNotification(data.message, data.status);
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

        // Clear token and re-enable UI
        delete window._pluginToggleRequests[pluginId];
        if (toggleCheckbox) {
            toggleCheckbox.disabled = false;
            toggleCheckbox.classList.remove('opacity-50', 'cursor-not-allowed');
        }
        if (wrapperDiv) {
            wrapperDiv.classList.remove('opacity-50', 'pointer-events-none');
        }
        // Resolve the outcome so callers (e.g. the install flow) can chain
        // on whether enabling actually succeeded.
        return data;
    })
    .catch(error => {
        // Verify this error is for the latest request (prevent race conditions)
        if (window._pluginToggleRequests[pluginId] !== requestToken) {
            debugLog(`[togglePlugin] Ignoring out-of-order error for ${pluginId}`);
            return;
        }

        showNotification('Error toggling plugin: ' + error.message, 'error');
        // Revert the toggle if API call failed
        if (plugin) {
            plugin.enabled = !enabled;
        }
        if (typeof loadInstalledPlugins === 'function') {
            loadInstalledPlugins();
        }

        // Clear token and re-enable UI
        delete window._pluginToggleRequests[pluginId];
        if (toggleCheckbox) {
            toggleCheckbox.disabled = false;
            toggleCheckbox.classList.remove('opacity-50', 'cursor-not-allowed');
        }
        if (wrapperDiv) {
            wrapperDiv.classList.remove('opacity-50', 'pointer-events-none');
        }
    });
};


// Track pending render data for when DOM isn't ready yet
window.__pendingInstalledPlugins = window.__pendingInstalledPlugins || null;
window.__pendingStorePlugins = window.__pendingStorePlugins || null;

// Document-level delegation for plugin card actions, so a card works even if
// it was rendered before the grid's own listener was attached. It hands the
// event to handlePluginAction, which the plugin-manager IIFE below exposes on
// window. (It used to test `typeof handlePluginAction`, which is IIFE-scoped
// and so never visible here: every click took a copied fallback instead, which
// asked to confirm an uninstall twice and sent Starlark app uninstalls to the
// plugin endpoint.)
(function setupGlobalEventDelegation() {
    const handleGlobalPluginAction = function(event) {
        const target = event.target;
        if (!target || typeof target.closest !== 'function') return;
        const el = target.closest('button[data-action][data-plugin-id]') ||
                   target.closest('input[data-action][data-plugin-id]');
        if (!el || typeof window.handlePluginAction !== 'function') return;
        window.handlePluginAction(event);
    };

    // Capture phase, so this runs before the grid's own listener;
    // handlePluginAction stops propagation, so an action is handled once.
    document.addEventListener('click', handleGlobalPluginAction, true);
    document.addEventListener('change', handleGlobalPluginAction, true);
    debugLog('[PLUGINS SCRIPT] Global event delegation set up');
})();

// Note: configurePlugin and togglePlugin are now defined at the top of the file (after uninstallPlugin)
// to ensure they're available immediately when the script loads

// Verify functions are defined (debug only)
if (_PLUGIN_DEBUG_EARLY) {
    debugLog('[PLUGINS SCRIPT] Functions defined:', {
        configurePlugin: typeof window.configurePlugin,
        togglePlugin: typeof window.togglePlugin
    });
    if (typeof window.configurePlugin === 'function') {
        debugLog('[PLUGINS SCRIPT] ✓ configurePlugin ready');
    }
    if (typeof window.togglePlugin === 'function') {
        debugLog('[PLUGINS SCRIPT] ✓ togglePlugin ready');
    }
}

// GitHub Token Collapse Handler - Define early so it's available before IIFE
debugLog('[DEFINE] Defining attachGithubTokenCollapseHandler function...');
window.attachGithubTokenCollapseHandler = function() {
    debugLog('[attachGithubTokenCollapseHandler] Starting...');
    const toggleTokenCollapseBtn = document.getElementById('toggle-github-token-collapse');
    debugLog('[attachGithubTokenCollapseHandler] Button found:', !!toggleTokenCollapseBtn);
    if (!toggleTokenCollapseBtn) {
        console.warn('[attachGithubTokenCollapseHandler] GitHub token collapse button not found');
        return;
    }

    debugLog('[attachGithubTokenCollapseHandler] Checking toggleGithubTokenContent...', {
        exists: typeof window.toggleGithubTokenContent
    });
    if (!window.toggleGithubTokenContent) {
        console.warn('[attachGithubTokenCollapseHandler] toggleGithubTokenContent function not defined');
        return;
    }

    // Remove any existing listeners by cloning the button
    const parent = toggleTokenCollapseBtn.parentNode;
    if (!parent) {
        console.warn('[attachGithubTokenCollapseHandler] Button parent not found');
        return;
    }

    const newBtn = toggleTokenCollapseBtn.cloneNode(true);
    parent.replaceChild(newBtn, toggleTokenCollapseBtn);

    // Attach listener to the new button
    newBtn.addEventListener('click', function(e) {
        debugLog('[attachGithubTokenCollapseHandler] Button clicked, calling toggleGithubTokenContent');
        window.toggleGithubTokenContent(e);
    });

    debugLog('[attachGithubTokenCollapseHandler] Handler attached to button:', newBtn.id);
};

// Toggle GitHub Token Settings section
debugLog('[DEFINE] Defining toggleGithubTokenContent function...');
window.toggleGithubTokenContent = function(e) {
    debugLog('[toggleGithubTokenContent] called', e);

    if (e) {
        e.stopPropagation();
        e.preventDefault();
    }

    const tokenContent = document.getElementById('github-token-content');
    const tokenIconCollapse = document.getElementById('github-token-icon-collapse');
    const toggleTokenCollapseBtn = document.getElementById('toggle-github-token-collapse');

    debugLog('[toggleGithubTokenContent] Elements found:', {
        tokenContent: !!tokenContent,
        tokenIconCollapse: !!tokenIconCollapse,
        toggleTokenCollapseBtn: !!toggleTokenCollapseBtn
    });

    if (!tokenContent || !toggleTokenCollapseBtn) {
        console.warn('[toggleGithubTokenContent] GitHub token content or button not found');
        return;
    }

    const hasHiddenClass = tokenContent.classList.contains('hidden');
    const computedDisplay = window.getComputedStyle(tokenContent).display;

    debugLog('[toggleGithubTokenContent] Current state:', {
        hasHiddenClass,
        computedDisplay,
        buttonText: toggleTokenCollapseBtn.querySelector('span')?.textContent
    });

    if (hasHiddenClass || computedDisplay === 'none') {
        // Show content - remove hidden class, add block class, remove inline display
        tokenContent.classList.remove('hidden');
        tokenContent.classList.add('block');
        tokenContent.style.removeProperty('display');
        if (tokenIconCollapse) {
            tokenIconCollapse.classList.remove('fa-chevron-down');
            tokenIconCollapse.classList.add('fa-chevron-up');
        }
        const span = toggleTokenCollapseBtn.querySelector('span');
        if (span) span.textContent = 'Collapse';
        debugLog('[toggleGithubTokenContent] Content shown - removed hidden, added block');
    } else {
        // Hide content - add hidden class, remove block class, ensure display is none
        tokenContent.classList.add('hidden');
        tokenContent.classList.remove('block');
        tokenContent.style.display = 'none';
        if (tokenIconCollapse) {
            tokenIconCollapse.classList.remove('fa-chevron-up');
            tokenIconCollapse.classList.add('fa-chevron-down');
        }
        const span = toggleTokenCollapseBtn.querySelector('span');
        if (span) span.textContent = 'Expand';
        debugLog('[toggleGithubTokenContent] Content hidden - added hidden, removed block, set display:none');
    }
};

// Simple standalone handler for GitHub plugin installation
// Defined early and globally to ensure it's always available
debugLog('[DEFINE] Defining handleGitHubPluginInstall function...');
window.handleGitHubPluginInstall = function() {
    debugLog('[handleGitHubPluginInstall] Function called!');

    const urlInput = document.getElementById('github-plugin-url');
    const statusDiv = document.getElementById('github-plugin-status');
    const branchInput = document.getElementById('plugin-branch-input');
    const installBtn = document.getElementById('install-plugin-from-url');

    if (!urlInput) {
        console.error('[handleGitHubPluginInstall] URL input not found');
        alert('Error: Could not find URL input field');
        return;
    }

    const repoUrl = urlInput.value.trim();
    debugLog('[handleGitHubPluginInstall] Repo URL:', repoUrl);

    if (!repoUrl) {
        if (statusDiv) {
            statusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a GitHub URL</span>';
        }
        return;
    }

    if (!isGithubUrl(repoUrl)) {
        if (statusDiv) {
            statusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a valid GitHub URL</span>';
        }
        return;
    }

    // Disable button and show loading
    if (installBtn) {
        installBtn.disabled = true;
        installBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Installing...';
    }
    if (statusDiv) {
        statusDiv.innerHTML = '<span class="text-blue-600"><i class="fas fa-spinner fa-spin mr-1"></i>Installing plugin...</span>';
    }

    const branch = branchInput?.value?.trim() || null;
    const requestBody = { repo_url: repoUrl };
    if (branch) {
        requestBody.branch = branch;
    }

    debugLog('[handleGitHubPluginInstall] Sending request:', requestBody);

    fetch('/api/v3/plugins/install-from-url', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(requestBody)
    })
    .then(response => {
        debugLog('[handleGitHubPluginInstall] Response status:', response.status);
        return response.json();
    })
    .then(data => {
        debugLog('[handleGitHubPluginInstall] Response data:', data);
        if (data.status === 'success') {
            if (statusDiv) {
                statusDiv.innerHTML = `<span class="text-green-600"><i class="fas fa-check-circle mr-1"></i>Successfully installed: ${window.LEDEscape.html(data.plugin_id)}</span>`;
            }
            urlInput.value = '';

            // Show notification if available
            showNotification(`Plugin ${data.plugin_id} installed successfully`, 'success');

            // Refresh installed plugins list if function available
            setTimeout(() => {
                if (typeof loadInstalledPlugins === 'function') {
                    loadInstalledPlugins();
                } else if (typeof window.loadInstalledPlugins === 'function') {
                    window.loadInstalledPlugins();
                }
            }, 1000);
        } else {
            if (statusDiv) {
                statusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>${window.LEDEscape.html(data.message || 'Installation failed')}</span>`;
            }
            showNotification(data.message || 'Installation failed', 'error');
        }
    })
    .catch(error => {
        console.error('[handleGitHubPluginInstall] Error:', error);
        if (statusDiv) {
            statusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>Error: ${window.LEDEscape.html(error.message)}</span>`;
        }
        showNotification('Error installing plugin: ' + error.message, 'error');
    })
    .finally(() => {
        if (installBtn) {
            installBtn.disabled = false;
            installBtn.innerHTML = '<i class="fas fa-download mr-2"></i>Install';
        }
    });
};
debugLog('[DEFINE] handleGitHubPluginInstall defined and ready');

// GitHub Authentication Status - Define early so it's available in IIFE
// Shows warning banner only when token is missing or invalid
// The token itself is never exposed to the frontend for security
// Returns a Promise so it can be awaited
debugLog('[DEFINE] Defining checkGitHubAuthStatus function...');
window.checkGitHubAuthStatus = function checkGitHubAuthStatus() {
    debugLog('[checkGitHubAuthStatus] Starting...');
    return fetch('/api/v3/plugins/store/github-status')
        .then(response => {
            debugLog('checkGitHubAuthStatus: Response status:', response.status);
            return response.json();
        })
        .then(data => {
            debugLog('checkGitHubAuthStatus: Data received:', data);
            if (data.status === 'success') {
                const authData = data.data;
                const tokenStatus = authData.token_status || (authData.authenticated ? 'valid' : 'none');
                debugLog('checkGitHubAuthStatus: Token status:', tokenStatus);
                const warning = document.getElementById('github-auth-warning');
                const settings = document.getElementById('github-token-settings');
                const rateLimit = document.getElementById('rate-limit-count');
                debugLog('checkGitHubAuthStatus: Elements found:', {
                    warning: !!warning,
                    settings: !!settings,
                    rateLimit: !!rateLimit
                });

                // Show warning only when token is missing ('none') or invalid ('invalid')
                if (tokenStatus === 'none' || tokenStatus === 'invalid') {
                    // Check if user has dismissed the warning (stored in session storage)
                    const dismissed = sessionStorage.getItem('github-auth-warning-dismissed');
                    if (!dismissed) {
                        if (warning && rateLimit) {
                            rateLimit.textContent = authData.rate_limit;

                            // Update warning message for invalid tokens
                            if (tokenStatus === 'invalid' && authData.error) {
                                const warningText = warning.querySelector('p.text-sm.text-yellow-700');
                                if (warningText) {
                                    // Clear existing content
                                    warningText.textContent = '';

                                    // Create safe error message with fallback
                                    const errorMsg = (authData.message || authData.error || 'Unknown error').toString();

                                    // Create <strong> element for "Token Invalid:" label
                                    const strong = document.createElement('strong');
                                    strong.textContent = 'Token Invalid:';

                                    // Create text node for error message and suffix
                                    const errorText = document.createTextNode(` ${errorMsg}. Please update your GitHub token to increase API rate limits to 5,000 requests/hour.`);

                                    // Append elements safely (no innerHTML)
                                    warningText.appendChild(strong);
                                    warningText.appendChild(errorText);
                                }
                            }
                            // For 'none' status, use the default message from HTML template

                            // Show warning using both classList and style.display
                            warning.classList.remove('hidden');
                            warning.style.display = '';
                            debugLog(`GitHub token status: ${tokenStatus} - showing API limit warning`);
                        }
                    }

                    // Ensure settings panel is accessible when token is missing or invalid
                    // Panel can be opened via "Configure Token" link in warning
                    // Don't force it to be visible, but don't prevent it from being shown
                } else if (tokenStatus === 'valid') {
                    // Token is valid - hide warning and ensure settings panel is visible but collapsed
                    if (warning) {
                        // Hide warning using both classList and style.display
                        warning.classList.add('hidden');
                        warning.style.display = 'none';
                        debugLog('GitHub token is valid - hiding API limit warning');
                    }

                    // Make settings panel visible but collapsed (accessible for token management)
                    if (settings) {
                        // Remove hidden class from panel itself - make it visible using both methods
                        settings.classList.remove('hidden');
                        settings.style.display = '';

                        // Always collapse the content when token is valid (user must click expand)
                        const tokenContent = document.getElementById('github-token-content');
                        if (tokenContent) {
                            // Collapse the content - add hidden, remove block, set display none
                            tokenContent.classList.add('hidden');
                            tokenContent.classList.remove('block');
                            tokenContent.style.display = 'none';
                        }

                        // Update collapse button state to show "Expand"
                        const tokenIconCollapse = document.getElementById('github-token-icon-collapse');
                        if (tokenIconCollapse) {
                            tokenIconCollapse.classList.remove('fa-chevron-up');
                            tokenIconCollapse.classList.add('fa-chevron-down');
                        }

                        const toggleTokenCollapseBtn = document.getElementById('toggle-github-token-collapse');
                        if (toggleTokenCollapseBtn) {
                            const span = toggleTokenCollapseBtn.querySelector('span');
                            if (span) span.textContent = 'Expand';

                            // Ensure event listener is attached
                            if (window.attachGithubTokenCollapseHandler) {
                                window.attachGithubTokenCollapseHandler();
                            }
                        }
                    }

                    // Clear dismissal flag when token becomes valid
                    sessionStorage.removeItem('github-auth-warning-dismissed');
                }
            }
        })
        .catch(error => {
            console.error('Error checking GitHub auth status:', error);
            console.error('Error stack:', error.stack || 'No stack trace');
        });
};

(function() {
    'use strict';

    if (_PLUGIN_DEBUG_EARLY) debugLog('Plugin manager script starting...');

    // Local variables for this instance
let installedPlugins = [];
window.currentPluginConfig = null;
    let pluginStoreCache = null; // Cache for plugin store to speed up subsequent loads
    let cacheTimestamp = null;
    const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes in milliseconds

    function storeCacheExpired() {
        return !cacheTimestamp || (Date.now() - cacheTimestamp >= CACHE_DURATION);
    }

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
window.pluginManager.initializing = false; // Track if initialization is in progress

// Initialize when DOM is ready or when HTMX loads content
window.initPluginsPage = function() {
    // Prevent duplicate initialization
    if (window.pluginManager.initialized || window.pluginManager.initializing) {
        debugLog('Plugin page already initialized or initializing, skipping...');
        return;
    }

    // Check if required elements exist
    const installedGrid = document.getElementById('installed-plugins-grid');
    if (!installedGrid) {
        debugLog('Plugin elements not ready yet');
        return false;
    }

    window.pluginManager.initializing = true;

    // Check GitHub auth status immediately (don't wait for full initialization)
    // This can run in parallel with other initialization
    if (window.checkGitHubAuthStatus) {
        debugLog('[INIT] Checking GitHub auth status immediately...');
        window.checkGitHubAuthStatus();
    }

    // If we fetched data before the DOM existed, render it now
    if (window.__pendingInstalledPlugins) {
        debugLog('[RENDER] Applying pending installed plugins data');
        window.__pendingInstalledPlugins = null;
        applyInstalledFiltersAndRender();
    }
    if (window.__pendingStorePlugins) {
        debugLog('[RENDER] Applying pending plugin store data');
        pluginStoreCache = window.__pendingStorePlugins;
        cacheTimestamp = Date.now();
        window.__pendingStorePlugins = null;
        applyStoreFiltersAndSort();
    }

    initializePlugins();

    // Event listeners (remove old ones first to prevent duplicates)
    const refreshBtn = document.getElementById('refresh-plugins-btn');
    const updateAllBtn = document.getElementById('update-all-plugins-btn');
    const restartBtn = document.getElementById('restart-display-btn');
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
    // Persisted store sort/perPage are restored by the controller's syncControls().
    setupInstalledFilterListeners();
    setupStoreFilterListeners();

    // The on-demand modal lives in base.html and survives tab swaps, so bind it
    // through the one guarded initializer instead of cloning its controls here
    // (cloning copied data-initialized and left two code paths owning listeners).
    if (closeOnDemandModalBtn || cancelOnDemandBtn || onDemandForm || onDemandModal) {
        initializeOnDemandModal();
    }

    // Load on-demand status silently (false = don't show notification)
    loadOnDemandStatus(false);
    startOnDemandStatusPolling();

    window.pluginManager.initialized = true;
    window.pluginManager.initializing = false;
    return true;
}

// Consolidated initialization function
function initializePluginPageWhenReady() {
    return window.initPluginsPage();
}

// Single initialization entry point
(function() {
    let initTimer = null;

    function attemptInit() {
        // Clear any pending timer
        if (initTimer) {
            clearTimeout(initTimer);
            initTimer = null;
        }

        // Try immediate initialization
        initializePluginPageWhenReady();
    }

    // Strategy 1: Immediate check (for direct page loads)
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        // DOM is already ready, try immediately with a small delay to ensure scripts are loaded
        initTimer = setTimeout(attemptInit, 50);
    } else {
        // Strategy 2: DOMContentLoaded (for direct page loads)
        document.addEventListener('DOMContentLoaded', function() {
            initTimer = setTimeout(attemptInit, 50);
        });
    }

    // Strategy 3: HTMX afterSwap event (for HTMX-loaded content)
    // This is the primary way plugins content is loaded
    // Register unconditionally — HTMX may load after this script (loaded dynamically from CDN)
    // CustomEvent listeners work even before HTMX is available
    document.body.addEventListener('htmx:afterSwap', function(event) {
        const target = event.detail.target;
        // Check if plugins content was swapped in (only match direct plugins content targets)
        if (target.id === 'plugins-content' ||
            target.querySelector('#installed-plugins-grid')) {
            debugLog('HTMX swap detected for plugins, initializing...');
            // Reset all initialization flags so the fresh empty DOM gets populated
            window.pluginManager.initialized = false;
            window.pluginManager.initializing = false;
            window.pluginManager._reswap = true; // signal: use cached store, don't re-fetch GitHub
            pluginsInitialized = false;
            initTimer = setTimeout(attemptInit, 100);
        }
    }, { once: false }); // Allow multiple swaps
})();

// Initialization guard to prevent multiple initializations
let pluginsInitialized = false;

function initializePlugins() {
    debugLog('[initializePlugins] FUNCTION CALLED, pluginsInitialized:', pluginsInitialized);
    // Guard against multiple initializations
    if (pluginsInitialized) {
        debugLog('[initializePlugins] Already initialized, skipping (but still setting up handlers)');
        // Still set up handlers even if already initialized (in case page was HTMX swapped)
        debugLog('[initializePlugins] Force setting up GitHub handlers anyway...');
        if (typeof setupGitHubInstallHandlers === 'function') {
            setupGitHubInstallHandlers();
        } else {
            console.error('[initializePlugins] setupGitHubInstallHandlers not found!');
        }
        return;
    }
    pluginsInitialized = true;

    debugLog('[initializePlugins] Starting initialization...');
    pluginLog('[INIT] Initializing plugins...');

    // Check GitHub authentication status
    debugLog('[INIT] Checking for checkGitHubAuthStatus function...', {
        exists: typeof window.checkGitHubAuthStatus,
        type: typeof window.checkGitHubAuthStatus
    });
    if (window.checkGitHubAuthStatus) {
        debugLog('[INIT] Calling checkGitHubAuthStatus...');
        try {
            window.checkGitHubAuthStatus();
        } catch (error) {
            console.error('[INIT] Error calling checkGitHubAuthStatus:', error);
        }
    } else {
        console.warn('[INIT] checkGitHubAuthStatus not available yet');
    }

    // Load both installed plugins and plugin store.
    // On HTMX re-swaps with a still-warm cache, skip GitHub metadata to avoid
    // re-hitting the API on every tab switch. If the cache TTL has expired even
    // during a re-swap, fetch fresh data including GitHub commit/version info.
    const isReswapWarm = !!window.pluginManager._reswap && !storeCacheExpired();
    window.pluginManager._reswap = false;

    // Fire both requests in parallel so the store doesn't wait for installed plugins.
    // The store renders install/update badges using window.installedPlugins || [] so
    // it works with an empty list. When installed plugins finish loading we do a
    // lightweight re-render from the already-cached store data to refresh the badges.
    searchPluginStore(!isReswapWarm);
    loadInstalledPlugins()
        .catch(err => console.error('[PluginStore] loadInstalledPlugins failed:', err))
        .then(() => {
            // Re-render store from cache to update install/update/reinstall badges now
            // that window.installedPlugins is populated. No network call — instant.
            if (typeof applyStoreFiltersAndSort === 'function') {
                applyStoreFiltersAndSort(true);
            }
        });

    // #plugin-search and #plugin-category are wired by the store's ListFilter
    // controller (setupStoreFilterListeners). They used to ALSO be bound here to
    // searchPluginStore; because that binding passed the DOM event as the
    // `fetchCommitInfo` argument, every keystroke and category change skipped the
    // cached-filter fast path and refetched /api/v3/plugins/store/list with commit
    // info. Filtering the cached list is the controller's job — leave it to it.

    // Setup GitHub installation handlers
    debugLog('[initializePlugins] About to call setupGitHubInstallHandlers...');
    if (typeof setupGitHubInstallHandlers === 'function') {
        debugLog('[initializePlugins] setupGitHubInstallHandlers is a function, calling it...');
        setupGitHubInstallHandlers();
        debugLog('[initializePlugins] setupGitHubInstallHandlers called');
    } else {
        console.error('[initializePlugins] ERROR: setupGitHubInstallHandlers is not a function! Type:', typeof setupGitHubInstallHandlers);
    }

    // Setup collapsible section handlers
    setupCollapsibleSections();

    // Load saved repositories
    loadSavedRepositories();

    pluginLog('[INIT] Plugins initialized');
}

// Track in-flight requests to prevent duplicates
// ===== PLUGIN LOADING WITH REQUEST DEDUPLICATION & CACHING =====
// Prevents redundant API calls by caching results for a short time
const pluginLoadCache = {
    promise: null,           // Current in-flight request
    data: null,              // Cached plugin data
    timestamp: 0,            // When cache was last updated
    TTL: 3000,               // Cache valid for 3 seconds
    isValid() {
        return this.data && (Date.now() - this.timestamp < this.TTL);
    },
    invalidate() {
        this.data = null;
        this.timestamp = 0;
    }
};

// Debug flag - set via safeLocalStorage.setItem('pluginDebug', 'true')
const PLUGIN_DEBUG = typeof localStorage !== 'undefined' && safeLocalStorage.getItem('pluginDebug') === 'true';
function pluginLog(...args) {
    if (PLUGIN_DEBUG) debugLog(...args);
}

function loadInstalledPlugins(forceRefresh = false) {
    // Return cached data if valid and not forcing refresh
    if (!forceRefresh && pluginLoadCache.isValid()) {
        pluginLog('[CACHE] Returning cached plugin data');
        renderInstalledPlugins(pluginLoadCache.data);
        return Promise.resolve(pluginLoadCache.data);
    }

    // If a request is already in progress, return the existing promise
    if (pluginLoadCache.promise) {
        pluginLog('[CACHE] Request in progress, returning existing promise');
        return pluginLoadCache.promise;
    }

    pluginLog('[FETCH] Loading installed plugins...');

    // Use PluginAPI if available, otherwise fall back to direct fetch
    const fetchPromise = (window.PluginAPI && window.PluginAPI.getInstalledPlugins) ?
        window.PluginAPI.getInstalledPlugins().then(plugins => {
            const pluginsArray = Array.isArray(plugins) ? plugins : [];
            return { status: 'success', data: { plugins: pluginsArray } };
        }) :
        fetch('/api/v3/plugins/installed').then(response => response.json());

    // Store the promise
    pluginLoadCache.promise = fetchPromise
        .then(data => {
            if (data.status === 'success') {
                const pluginsData = data.data?.plugins;
                installedPlugins = Array.isArray(pluginsData) ? pluginsData : [];

                // Update cache
                pluginLoadCache.data = installedPlugins;
                pluginLoadCache.timestamp = Date.now();

                pluginLog('[FETCH] Loaded', installedPlugins.length, 'plugins');

                // Debug logging only when enabled
                if (PLUGIN_DEBUG) {
                    installedPlugins.forEach(plugin => {
                        debugLog(`[DEBUG] Plugin ${plugin.id}: enabled=${plugin.enabled}`);
                    });
                }

                // Also refreshes the '#installed-count' text via the filter controller.
                renderInstalledPlugins(installedPlugins);

                return installedPlugins;
            } else {
                throw new Error('Failed to load installed plugins: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error loading installed plugins:', error);
            let errorMsg = 'Error loading plugins: ' + error.message;
            if (isNetworkFailure(error)) {
                errorMsg += ' - Please try refreshing your browser.';
            }
            // Replace the whole panel only when there is nothing to show yet; a
            // failed refresh keeps the grid and the store the user is looking at.
            if (pluginLoadCache.data === null) {
                showInstalledLoadError(errorMsg);
            } else {
                showNotification(errorMsg, 'error');
            }
            throw error;
        })
        .finally(() => {
            // Clear the in-flight promise (but keep cache data)
            pluginLoadCache.promise = null;
        });

    return pluginLoadCache.promise;
}

// Force refresh function for explicit user actions
function refreshInstalledPlugins() {
    pluginLoadCache.invalidate();
    return loadInstalledPlugins(true);
}

// Expose loadInstalledPlugins on window.pluginManager for Alpine.js integration
window.pluginManager.loadInstalledPlugins = loadInstalledPlugins;
// Note: searchPluginStore will be exposed after its definition (see below)

// ── Installed Plugins: search / filter / sort ───────────────────────────
// Sort comparators for the installed-plugins toolbar. Kept beside the render
// code they drive rather than inside the ListFilter helper, which stays
// section-agnostic.
function installedSortName(plugin) {
    return String((plugin && (plugin.name || plugin.id)) || '').toLowerCase();
}

// last_updated is the plugin's local git commit date (date_iso from
// _get_local_git_info, see api_v3.py). It can be absent or unparseable.
function installedUpdatedAt(plugin) {
    const raw = plugin ? plugin.last_updated : null;
    if (!raw) return null;
    const t = Date.parse(raw);
    return Number.isNaN(t) ? null : t;
}

const INSTALLED_COMPARATORS = {
    'a-z': (a, b) => installedSortName(a).localeCompare(installedSortName(b)),
    'z-a': (a, b) => installedSortName(b).localeCompare(installedSortName(a)),
    'status': (a, b) => {
        const rank = p => (p.update_available ? 0 : (p.enabled ? 1 : 2));
        const diff = rank(a) - rank(b);
        return diff !== 0 ? diff : installedSortName(a).localeCompare(installedSortName(b));
    },
    'recent': (a, b) => {
        const ta = installedUpdatedAt(a);
        const tb = installedUpdatedAt(b);
        // Plugins with no usable timestamp sort to the end, never to the top.
        if (ta === null && tb === null) return installedSortName(a).localeCompare(installedSortName(b));
        if (ta === null) return 1;
        if (tb === null) return -1;
        return tb - ta;
    },
    'category': (a, b) => {
        const catCmp = String(a.category || '').localeCompare(String(b.category || ''));
        return catCmp !== 0 ? catCmp : installedSortName(a).localeCompare(installedSortName(b));
    },
};

let _installedFilter = null;

// Created lazily so a script load-order problem degrades to "no filtering"
// instead of throwing while this file is still being evaluated.
function getInstalledFilter() {
    if (_installedFilter) return _installedFilter;
    if (!window.ListFilter || typeof window.ListFilter.create !== 'function') {
        console.warn('[PLUGINS] ListFilter helper unavailable — installed plugin filters disabled');
        return null;
    }
    _installedFilter = window.ListFilter.create({
        // Always read the canonical list, never a captured snapshot.
        getItems: () => window.installedPlugins || installedPlugins || [],
        render: renderInstalledCards,
        search: {
            el: 'installed-search',
            clearEl: 'installed-search-clear',
            fields: ['name', 'id', 'description', 'author', 'category', 'tags'],
            debounceMs: 150,
        },
        sort: {
            el: 'installed-sort',
            default: 'a-z',
            comparators: INSTALLED_COMPARATORS,
        },
        controls: [{
            type: 'pills',
            el: '#installed-filter-pills',
            attr: 'data-installed-filter',
            key: 'status',
            default: 'all',
            test: (plugin, value) => {
                if (value === 'enabled') return Boolean(plugin.enabled);
                if (value === 'disabled') return !plugin.enabled;
                // update_available is computed server-side (api_v3.py) — never
                // recompute it from version strings here.
                if (value === 'updates') return Boolean(plugin.update_available);
                return true;
            },
        }],
        clearEl: 'installed-clear-filters',
        countEl: 'installed-count',
        countFormat: (shown, total, filtered) =>
            filtered ? `${shown} of ${total} shown` : `${total} installed`,
        onChrome: updateInstalledUpdatesBadge,
    });
    return _installedFilter;
}

// The Updates pill counts against the *full* list, not the filtered view, so
// the badge keeps telling you how much work is outstanding while you browse.
function updateInstalledUpdatesBadge() {
    const all = window.installedPlugins || installedPlugins || [];
    const n = all.filter(p => p && p.update_available).length;

    const badge = document.getElementById('installed-updates-count');
    if (badge) {
        badge.textContent = String(n);
        badge.classList.toggle('hidden', n === 0);
    }

    const pill = document.querySelector('#installed-filter-pills [data-installed-filter="updates"]');
    if (pill) {
        pill.classList.toggle('opacity-50', n === 0);
        pill.title = n === 0
            ? 'No plugins have updates available'
            : `Show only the ${n} plugin${n !== 1 ? 's' : ''} with a newer version available`;
    }
}

function applyInstalledFiltersAndRender() {
    const ctl = getInstalledFilter();
    if (ctl) {
        ctl.apply();
        return;
    }
    // Fallback: render everything, so the section still works without the helper.
    const all = window.installedPlugins || installedPlugins || [];
    renderInstalledCards(all, all.length);
    const countEl = document.getElementById('installed-count');
    if (countEl) countEl.textContent = all.length + ' installed';
}

function setupInstalledFilterListeners() {
    const ctl = getInstalledFilter();
    if (!ctl) return;
    // Both are idempotent — the plugins partial is HTMX-swapped, so this runs
    // again on every return to the tab.
    ctl.bind();
    ctl.syncControls();
}

// The one place the installed-plugin list is published, then rendered
// through the active filters. It sets window.installedPlugins, which the
// toggle handler, isStorePluginInstalled and runUpdateAllPlugins read, and
// dispatches one pluginsUpdated event, on which the Alpine app (app-shell.js)
// takes the list and rebuilds the plugin tab row. Both must get the FULL
// list, never a filtered subset.
function renderInstalledPlugins(plugins) {
    window.installedPlugins = plugins;
    document.dispatchEvent(new CustomEvent('pluginsUpdated', { detail: { plugins: plugins } }));

    // The Plugin Manager tab may not be loaded yet; initPluginsPage renders
    // when it is.
    if (!document.getElementById('installed-plugins-grid')) {
        pluginLog('[RENDER] installed-plugins-grid not loaded yet, rendering when the tab loads');
        window.__pendingInstalledPlugins = plugins;
        return;
    }
    applyInstalledFiltersAndRender();
}

// Renders the card grid only. `plugins` is the visible (filtered) subset and
// `total` the full installed count — this must NOT touch window.installedPlugins.
function renderInstalledCards(plugins, total) {
    const container = document.getElementById('installed-plugins-grid');
    if (!container) return;

    const totalCount = (typeof total === 'number') ? total : plugins.length;

    // Remove skeleton cards before rendering real content
    container.querySelectorAll('.installed-skeleton').forEach(el => el.remove());

    // Attached before the early returns so the empty state's Clear button works.
    setupInstalledEventDelegation();

    if (plugins.length === 0) {
        container.innerHTML = totalCount === 0 ? `
            <div class="col-span-full empty-state">
                <div class="empty-state-icon">
                    <i class="fas fa-plug"></i>
                </div>
                <p class="text-lg font-medium text-gray-700 mb-1">No plugins installed</p>
                <p class="text-sm text-gray-500">Install plugins from the store to get started</p>
            </div>
        ` : `
            <div class="col-span-full empty-state">
                <div class="empty-state-icon">
                    <i class="fas fa-filter"></i>
                </div>
                <p class="text-lg font-medium text-gray-700 mb-1">No plugins match your filters</p>
                <p class="text-sm text-gray-500 mb-4">${totalCount} plugin${totalCount !== 1 ? 's' : ''} installed, none matching the current search or filters.</p>
                <button class="btn bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-semibold"
                        data-action="clear-installed-filters">
                    <i class="fas fa-times mr-2"></i>Clear filters
                </button>
            </div>
        `;
        return;
    }

    setGridHtmlIfChanged(container, plugins.map(plugin => {
        // Convert enabled to boolean for consistent rendering
        const enabledBool = Boolean(plugin.enabled);

        // Debug: Log enabled status during rendering (only when debug enabled)
        if (PLUGIN_DEBUG) {
            debugLog(`[DEBUG RENDER] Plugin ${plugin.id}: enabled=${enabledBool}`);
        }

        // Escape plugin ID for use in HTML attributes and JavaScript
        const escapedPluginId = escapeAttribute(plugin.id);

        return `
        <div class="plugin-card">
            <div class="mb-4">
                <!-- Header row: only the name and badges share a line with the
                     toggle, so the metadata and description below get the
                     card's full width. -->
                <div class="flex items-start justify-between gap-3 mb-2">
                    <div class="flex items-center flex-wrap gap-2 min-w-0">
                        <h4 class="font-semibold text-gray-900 text-base">${escapeHtml(plugin.name || plugin.id)}</h4>
                        ${plugin.is_starlark_app ? '<span class="badge badge-warning"><i class="fas fa-star mr-1"></i>Starlark</span>' : ''}
                        ${plugin.verified ? '<span class="badge badge-success"><i class="fas fa-check-circle mr-1"></i>Verified</span>' : ''}
                    </div>
                <!-- Toggle Switch in Top Right -->
                <div class="flex-shrink-0">
                    <label class="relative inline-flex items-center cursor-pointer group">
                        <!-- input.peer must stay immediately before the visible pill:
                             peer-focus:* only reaches a following sibling. -->
                        <input type="checkbox"
                               class="sr-only peer"
                               role="switch"
                               aria-label="Enable ${escapeAttribute(plugin.name || plugin.id)}"
                               id="toggle-${escapedPluginId}"
                               ${enabledBool ? 'checked' : ''}
                               data-plugin-id="${escapedPluginId}"
                               data-action="toggle">
                        <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg border-2 transition-all duration-200 ${enabledBool ? 'bg-green-50 border-green-500' : 'bg-gray-50 border-gray-300'} hover:shadow-md group-hover:scale-105 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300" aria-hidden="true">
                            <!-- Toggle Switch -->
                            <div class="relative w-14 h-7 ${enabledBool ? 'bg-green-500' : 'bg-gray-300'} rounded-full transition-colors duration-200 ease-in-out shadow-inner">
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
                <div class="text-sm text-gray-600 space-y-1.5 mb-3">
                    <p class="flex items-center"><i class="fas fa-user mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.author || 'Unknown')}</p>
                    ${plugin.version ? `<p class="flex items-center flex-wrap gap-1.5"><i class="fas fa-tag mr-2 text-gray-400 w-4"></i>v${escapeHtml(plugin.version)}${plugin.update_available && plugin.latest_version ? `<span class="badge badge-info" title="Installed v${escapeAttribute(plugin.version)} → latest v${escapeAttribute(plugin.latest_version)}"><i class="fas fa-arrow-circle-up mr-1"></i>v${escapeHtml(plugin.latest_version)} available</span>` : ''}</p>` : ''}
                    <p class="flex items-center"><i class="fas fa-folder mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.category || 'General')}</p>
                </div>
                <p class="text-sm text-gray-700 leading-relaxed">${escapeHtml(plugin.description || 'No description available')}</p>
            </div>

            <!-- Plugin Tags -->
            ${plugin.tags && plugin.tags.length > 0 ? `
                <div class="flex flex-wrap gap-1.5 mb-4">
                    ${plugin.tags.map(tag => `<span class="badge badge-info">${escapeHtml(tag)}</span>`).join('')}
                </div>
            ` : ''}

            <!-- Plugin Actions -->
            <div style="display: flex; flex-direction: column; gap: 0.5rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #e5e7eb;">
                <button class="btn bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-semibold"
                        style="display: flex; width: 100%; justify-content: center;"
                        data-plugin-id="${escapedPluginId}"
                        data-action="configure">
                    <i class="fas fa-cog mr-2"></i>Configure
                </button>
                <div style="display: flex; gap: 0.5rem;">
                    <button class="btn ${plugin.update_available ? 'bg-blue-600 hover:bg-blue-700 plugin-update-available' : 'bg-yellow-600 hover:bg-yellow-700'} text-white px-4 py-2 rounded-md text-sm font-semibold"
                            style="flex: 1;"
                            data-plugin-id="${escapedPluginId}"
                            data-action="update"
                            title="${plugin.update_available && plugin.latest_version ? 'Update to v' + escapeAttribute(plugin.latest_version) : 'Reinstall the latest published version'}">
                        <i class="fas ${plugin.update_available ? 'fa-arrow-circle-up' : 'fa-sync'} mr-2"></i>${plugin.update_available && plugin.latest_version ? 'Update to v' + escapeHtml(plugin.latest_version) : 'Update'}
                    </button>
                    <button class="btn bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-md text-sm font-semibold"
                            style="flex: 1;"
                            data-plugin-id="${escapedPluginId}"
                            data-action="uninstall">
                        <i class="fas fa-trash mr-2"></i>Uninstall
                    </button>
                </div>
            </div>
        </div>
        `;
    }).join(''));
}

// Filter/search changes re-run the card renderers. When the markup they
// produce is identical to what is already in the grid (same visible set, same
// state), skip the innerHTML rebuild: it saves layout work on small Pis and
// keeps focus and any typed branch names in place. The cache lives on the
// element, so an HTMX swap that replaces the grid starts fresh, and it is
// tied to the first node it produced, so any other write to the grid (empty
// state, loading skeleton, error message) invalidates it automatically.
function setGridHtmlIfChanged(container, html) {
    if (container._lastRenderedHtml === html &&
        container._lastRenderedFirst &&
        container._lastRenderedFirst === container.firstElementChild) {
        return false;
    }
    container.innerHTML = html;
    container._lastRenderedHtml = html;
    container._lastRenderedFirst = container.firstElementChild;
    return true;
}
// Shared with the Starlark section, which lives in a separate IIFE below.
window.__pmSetGridHtmlIfChanged = setGridHtmlIfChanged;

// Set up event delegation for plugin action buttons (fallback if onclick doesn't work)
// Only set up once per container to avoid redundant listeners, which is what
// makes it safe to rebuild the grid's innerHTML on every keystroke.
function setupInstalledEventDelegation() {
    const container = document.getElementById('installed-plugins-grid');
    if (!container) {
        pluginLog('[RENDER] installed-plugins-grid not found for event delegation');
        return;
    }

    // Skip if already set up (guard against multiple calls)
    if (container._eventDelegationSetup) {
        return;
    }

    // Mark as set up
    container._eventDelegationSetup = true;
    container._pluginActionHandler = handlePluginAction;

    // Add listeners for both click and change events
    container.addEventListener('click', handlePluginAction, true);
    container.addEventListener('change', handlePluginAction, true);
    pluginLog('[RENDER] Event delegation set up for installed-plugins-grid');
}


function handlePluginAction(event) {
    // Check for both button and input (for toggle)
    const button = event.target.closest('button[data-action]') || event.target.closest('input[data-action]');
    if (!button) return;

    const action = button.getAttribute('data-action');
    const pluginId = button.getAttribute('data-plugin-id');

    // Grid-level actions have no plugin id (the empty state's Clear button).
    if (action === 'clear-installed-filters') {
        event.preventDefault();
        event.stopPropagation();
        const ctl = getInstalledFilter();
        if (ctl) ctl.reset();
        return;
    }

    if (!pluginId) return;

    event.preventDefault();
    event.stopPropagation();

    debugLog('[EVENT DELEGATION] Plugin action:', action, 'Plugin ID:', pluginId);

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
            // Toggling under an Enabled/Disabled filter would otherwise make the
            // card vanish the moment the server confirms. Pin it until the user
            // next touches the toolbar.
            {
                const ctl = getInstalledFilter();
                if (ctl) ctl.sticky.add(pluginId);
            }
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

            debugLog('[DEBUG toggle] Plugin:', pluginId, 'Current enabled (from data):', currentEnabled, 'New state:', isChecked, 'Event type:', event.type);

            waitForFunction('togglePlugin', 10, 50)
                .then(toggleFunc => {
                    toggleFunc(pluginId, isChecked);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    showNotification('Toggle function not loaded. Please refresh the page.', 'error');
                });
            break;
        case 'configure':
            waitForFunction('configurePlugin', 10, 50)
                .then(configureFunc => {
                    configureFunc(pluginId);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    showNotification('Configure function not loaded. Please refresh the page.', 'error');
                });
            break;
        case 'update':
            waitForFunction('updatePlugin', 10, 50)
                .then(updateFunc => {
                    updateFunc(pluginId);
                })
                .catch(error => {
                    console.error('[EVENT DELEGATION]', error.message);
                    showNotification('Update function not loaded. Please refresh the page.', 'error');
                });
            break;
        case 'uninstall':
            if (pluginId.startsWith('starlark:')) {
                // Starlark app uninstall uses dedicated endpoint
                const starlarkAppId = pluginId.slice('starlark:'.length);
                if (!confirm(`Uninstall Starlark app "${starlarkAppId}"?`)) break;
                fetch(`/api/v3/starlark/apps/${encodeURIComponent(starlarkAppId)}`, {method: 'DELETE'})
                    .then(r => r.json())
                    .then(data => {
                        if (data.status === 'success') {
                            showNotification('Starlark app uninstalled', 'success');
                            if (typeof loadInstalledPlugins === 'function') loadInstalledPlugins();
                            else if (typeof window.loadInstalledPlugins === 'function') window.loadInstalledPlugins();
                        } else {
                            alert('Uninstall failed: ' + (data.message || 'Unknown error'));
                        }
                    })
                    .catch(err => alert('Uninstall failed: ' + err.message));
            } else {
                waitForFunction('uninstallPlugin', 10, 50)
                    .then(uninstallFunc => {
                        uninstallFunc(pluginId);
                    })
                    .catch(error => {
                        console.error('[EVENT DELEGATION]', error.message);
                        showNotification('Uninstall function not loaded. Please refresh the page.', 'error');
                    });
            }
            break;
    }
}
window.handlePluginAction = handlePluginAction;

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
                if (fromRefreshButton) {
                    showNotification('On-demand status refreshed', 'success');
                }
            } else {
                const message = result.message || 'Failed to load on-demand status';
                setOnDemandError(message);
                showNotification(message, 'error');
            }
        })
        .catch(error => {
            console.error('Error fetching on-demand status:', error);
            setOnDemandError(error?.message || 'Error fetching on-demand status');
            showNotification('Error fetching on-demand status: ' + error.message, 'error');
        });
}

// The 15s on-demand status poll only runs while someone can see it: the page
// is visible and the Plugin Manager tab is the active one. On a Pi Zero 2 W a
// forgotten background tab should cost nothing. When it becomes visible again
// it refreshes immediately, then resumes the interval.
const ON_DEMAND_POLL_MS = 15000;

function isPluginManagerTabVisible() {
    if (document.hidden) return false;
    try {
        if (window.Alpine && typeof window.Alpine.$data === 'function') {
            const data = window.Alpine.$data(document.body);
            if (data && typeof data.activeTab === 'string') {
                return data.activeTab === 'plugins';
            }
        }
    } catch (e) {
        // Alpine not initialised on body yet; fall through to the DOM check.
    }
    const content = document.getElementById('plugins-content');
    return !!(content && content.offsetParent !== null);
}

function syncOnDemandStatusPolling() {
    const shouldRun = isPluginManagerTabVisible();
    if (shouldRun && !onDemandStatusInterval) {
        loadOnDemandStatus(false);
        onDemandStatusInterval = setInterval(() => {
            if (!isPluginManagerTabVisible()) {
                clearInterval(onDemandStatusInterval);
                onDemandStatusInterval = null;
                return;
            }
            loadOnDemandStatus(false);
        }, ON_DEMAND_POLL_MS);
    } else if (!shouldRun && onDemandStatusInterval) {
        clearInterval(onDemandStatusInterval);
        onDemandStatusInterval = null;
    }
}

function startOnDemandStatusPolling() {
    if (onDemandStatusInterval) {
        clearInterval(onDemandStatusInterval);
        onDemandStatusInterval = null;
    }
    // The caller has just loaded status, so start the interval without a
    // second immediate fetch; later resumes go through syncOnDemandStatusPolling.
    if (isPluginManagerTabVisible()) {
        onDemandStatusInterval = setInterval(() => {
            if (!isPluginManagerTabVisible()) {
                clearInterval(onDemandStatusInterval);
                onDemandStatusInterval = null;
                return;
            }
            loadOnDemandStatus(false);
        }, ON_DEMAND_POLL_MS);
    }

    if (!window.__onDemandPollingListenersBound) {
        window.__onDemandPollingListenersBound = true;
        document.addEventListener('visibilitychange', syncOnDemandStatusPolling);
        // Tab switches: nav clicks change Alpine's activeTab, and HTMX swaps
        // tab content in. Defer so Alpine has applied x-show first.
        document.addEventListener('click', () => setTimeout(syncOnDemandStatusPolling, 0));
        document.addEventListener('htmx:afterSettle', syncOnDemandStatusPolling);
        window.addEventListener('popstate', () => setTimeout(syncOnDemandStatusPolling, 0));
    }
}

window.loadOnDemandStatus = loadOnDemandStatus;

function runUpdateAllPlugins() {
    const button = document.getElementById('update-all-plugins-btn');

    if (!button) {
        showNotification('Unable to locate bulk update controls. Refresh the Plugin Manager tab.', 'error');
        return;
    }

    if (button.dataset.running === 'true') {
        return;
    }

    const plugins = Array.isArray(window.installedPlugins) ? window.installedPlugins : [];
    if (!plugins.length) {
        showNotification('No installed plugins to update.', 'warning');
        return;
    }

    const originalContent = button.innerHTML;
    button.dataset.running = 'true';
    button.disabled = true;
    button.classList.add('opacity-60', 'cursor-wait');
    button.innerHTML = '<i class="fas fa-sync fa-spin mr-2"></i>Checking...';

    const onProgress = (current, total, pluginId) => {
        button.innerHTML = `<i class="fas fa-sync fa-spin mr-2"></i>Updating ${current}/${total}...`;
    };

    Promise.resolve(window.updateAllPlugins(onProgress))
        .then(results => {
            if (!results || !results.length) {
                showNotification('No plugins to update.', 'info');
                return;
            }
            // Counted by install_manager.js from each answer's update_status:
            // a no-op update is "already up to date", not "updated". A cached
            // install_manager.js from before that helper gets a plain count.
            const manager = window.PluginInstallManager;
            const summary = (manager && typeof manager.summarizeUpdateResults === 'function')
                ? manager.summarizeUpdateResults(results)
                : (() => {
                    const failed = results.filter(r => !r.success).length;
                    const checked = results.length - failed;
                    return {
                        text: `${checked} checked` + (failed ? `, ${failed} failed` : ''),
                        type: failed ? (checked ? 'warning' : 'error') : 'success'
                    };
                })();
            showNotification(summary.text, summary.type);
        })
        .catch(error => {
            console.error('Error updating all plugins:', error);
            showNotification('Error updating all plugins: ' + error.message, 'error');
        })
        .finally(() => {
            button.innerHTML = originalContent;
            button.disabled = false;
            button.classList.remove('opacity-60', 'cursor-wait');
            button.dataset.running = 'false';
        });
}

// Initialize on-demand modal setup (runs unconditionally since modal is in base.html)
function initializeOnDemandModal() {
    const closeOnDemandModalBtn = document.getElementById('close-on-demand-modal');
    const cancelOnDemandBtn = document.getElementById('cancel-on-demand');
    const onDemandForm = document.getElementById('on-demand-form');
    const onDemandModal = document.getElementById('on-demand-modal');

    if (closeOnDemandModalBtn && !closeOnDemandModalBtn.dataset.initialized) {
        closeOnDemandModalBtn.replaceWith(closeOnDemandModalBtn.cloneNode(true));
        const newBtn = document.getElementById('close-on-demand-modal');
        if (newBtn) {
            newBtn.dataset.initialized = 'true';
            newBtn.addEventListener('click', closeOnDemandModal);
        }
    }
    if (cancelOnDemandBtn && !cancelOnDemandBtn.dataset.initialized) {
        cancelOnDemandBtn.replaceWith(cancelOnDemandBtn.cloneNode(true));
        const newBtn = document.getElementById('cancel-on-demand');
        if (newBtn) {
            newBtn.dataset.initialized = 'true';
            newBtn.addEventListener('click', closeOnDemandModal);
        }
    }
    if (onDemandForm && !onDemandForm.dataset.initialized) {
        onDemandForm.replaceWith(onDemandForm.cloneNode(true));
        const newForm = document.getElementById('on-demand-form');
        if (newForm) {
            newForm.dataset.initialized = 'true';
            newForm.addEventListener('submit', submitOnDemandRequest);
        }
    }
    if (onDemandModal && !onDemandModal.dataset.initialized) {
        onDemandModal.dataset.initialized = 'true';
        onDemandModal.onclick = closeOnDemandModalOnBackdrop;
    }
}

// Store the real implementation and replace the stub
window.__openOnDemandModalImpl = function(pluginId) {
    debugLog('[__openOnDemandModalImpl] Called with pluginId:', pluginId);
    const plugin = findInstalledPlugin(pluginId);
    debugLog('[__openOnDemandModalImpl] Found plugin:', plugin ? plugin.id : 'NOT FOUND');
    if (!plugin) {
        console.warn('[__openOnDemandModalImpl] Plugin not found, installedPlugins:', window.installedPlugins?.length || 0);
        showNotification(`Plugin ${pluginId} not found`, 'error');
        return;
    }

    // Note: On-demand can work with disabled plugins - the backend will temporarily enable them
    // We still log it for debugging but don't block the modal
    if (!plugin.enabled) {
        debugLog('[__openOnDemandModalImpl] Plugin is disabled, but on-demand will temporarily enable it');
    }

    currentOnDemandPluginId = pluginId;
    debugLog('[__openOnDemandModalImpl] Setting currentOnDemandPluginId to:', pluginId);

    // Ensure modal is initialized
    debugLog('[__openOnDemandModalImpl] Initializing modal...');
    initializeOnDemandModal();

    const modal = document.getElementById('on-demand-modal');
    const modeSelect = document.getElementById('on-demand-mode');
    const modeHint = document.getElementById('on-demand-mode-hint');
    const durationInput = document.getElementById('on-demand-duration');
    const pinnedCheckbox = document.getElementById('on-demand-pinned');
    const startServiceCheckbox = document.getElementById('on-demand-start-service');
    const modalTitle = document.getElementById('on-demand-modal-title');

    debugLog('[__openOnDemandModalImpl] Modal elements check:', {
        modal: !!modal,
        modeSelect: !!modeSelect,
        modeHint: !!modeHint,
        durationInput: !!durationInput,
        pinnedCheckbox: !!pinnedCheckbox,
        startServiceCheckbox: !!startServiceCheckbox,
        modalTitle: !!modalTitle
    });

    if (!modal || !modeSelect || !modeHint || !durationInput || !pinnedCheckbox || !startServiceCheckbox || !modalTitle) {
        console.error('On-demand modal elements not found', {
            modal: !!modal,
            modeSelect: !!modeSelect,
            modeHint: !!modeHint,
            durationInput: !!durationInput,
            pinnedCheckbox: !!pinnedCheckbox,
            startServiceCheckbox: !!startServiceCheckbox,
            modalTitle: !!modalTitle
        });
        return;
    }

    debugLog('[__openOnDemandModalImpl] All elements found, opening modal...');

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

    debugLog('[__openOnDemandModalImpl] Setting modal display to flex');
    // Force modal to be visible and properly positioned
    // Remove all inline styles that might interfere
    modal.removeAttribute('style');
    // Set explicit positioning to ensure it's visible
    modal.style.cssText = 'position: fixed !important; top: 0 !important; left: 0 !important; right: 0 !important; bottom: 0 !important; display: flex !important; visibility: visible !important; opacity: 1 !important; z-index: 9999 !important; margin: 0 !important; padding: 0 !important;';

    // Ensure modal content is centered
    const modalContent = modal.querySelector('.modal-content');
    if (modalContent) {
        modalContent.style.margin = 'auto';
        modalContent.style.maxHeight = '90vh';
        modalContent.style.overflowY = 'auto';
    }

    // Dialog semantics, focus trap, Escape, and focus return. Opening again
    // while already open (e.g. a double click) keeps the existing trap so
    // focus still returns to the control that first opened it.
    if (modalContent && window.LEDDialog && !onDemandDialogRelease) {
        onDemandDialogRelease = window.LEDDialog.trap(modalContent, {
            labelledBy: 'on-demand-modal-title',
            initialFocus: modeSelect,
            onEscape: closeOnDemandModal
        });
    }

    // Scroll to top of page to ensure modal is visible
    window.scrollTo({ top: 0, behavior: 'smooth' });

    // Force a reflow to ensure styles are applied
    modal.offsetHeight;
    debugLog('[__openOnDemandModalImpl] Modal display set, should be visible now. Modal element:', modal);
    debugLog('[__openOnDemandModalImpl] Modal computed styles:', {
        display: window.getComputedStyle(modal).display,
        visibility: window.getComputedStyle(modal).visibility,
        opacity: window.getComputedStyle(modal).opacity,
        zIndex: window.getComputedStyle(modal).zIndex,
        position: window.getComputedStyle(modal).position
    });
    // Also check if modal is actually in the viewport
    const rect = modal.getBoundingClientRect();
    debugLog('[__openOnDemandModalImpl] Modal bounding rect:', {
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
        visible: rect.width > 0 && rect.height > 0
    });
};

window.openOnDemandModal = window.__openOnDemandModalImpl;

// Release handle for the on-demand modal's focus trap (window.LEDDialog).
// Every close path funnels through closeOnDemandModal, which releases it once.
let onDemandDialogRelease = null;

function closeOnDemandModal() {
    const modal = document.getElementById('on-demand-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    currentOnDemandPluginId = null;
    if (onDemandDialogRelease) {
        const release = onDemandDialogRelease;
        onDemandDialogRelease = null;
        release();
    }
}

function submitOnDemandRequest(event) {
    event.preventDefault();
    debugLog('[submitOnDemandRequest] Form submitted, currentOnDemandPluginId:', currentOnDemandPluginId);

    if (!currentOnDemandPluginId) {
        console.error('[submitOnDemandRequest] No plugin ID set');
        showNotification('Select a plugin before starting on-demand mode.', 'error');
        return;
    }

    const form = document.getElementById('on-demand-form');
    if (!form) {
        console.error('[submitOnDemandRequest] Form not found');
        return;
    }

    debugLog('[submitOnDemandRequest] Form found, processing...');

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

    debugLog('[submitOnDemandRequest] Payload:', payload);
    markOnDemandLoading();

    fetch('/api/v3/display/on-demand/start', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    })
        .then(response => {
            debugLog('[submitOnDemandRequest] Response status:', response.status);
            return response.json();
        })
        .then(result => {
            debugLog('[submitOnDemandRequest] Response data:', result);
            if (result.status === 'success') {
                const pluginName = resolvePluginDisplayName(currentOnDemandPluginId);
                showNotification(`Requested on-demand mode for ${pluginName}`, 'success');
                closeOnDemandModal();
                setTimeout(() => loadOnDemandStatus(true), 700);
            } else {
                console.error('[submitOnDemandRequest] Request failed:', result);
                showNotification(result.message || 'Failed to start on-demand mode', 'error');
            }
        })
        .catch(error => {
            console.error('[submitOnDemandRequest] Error starting on-demand mode:', error);
            showNotification('Error starting on-demand mode: ' + error.message, 'error');
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
                const message = stopService
                    ? 'On-demand mode stop requested and display service will be stopped.'
                    : 'On-demand mode stop requested';
                showNotification(message, 'success');
                setTimeout(() => loadOnDemandStatus(true), 700);
            } else {
                showNotification(result.message || 'Failed to stop on-demand mode', 'error');
            }
        })
        .catch(error => {
            console.error('Error stopping on-demand mode:', error);
            showNotification('Error stopping on-demand mode: ' + error.message, 'error');
        });
}

function stopOnDemand(event) {
    const stopService = event && event.shiftKey;
    requestOnDemandStop({ stopService });
}

window.requestOnDemandStop = requestOnDemandStop;

function closeOnDemandModalOnBackdrop(event) {
    if (event.target === event.currentTarget) {
        closeOnDemandModal();
    }
}

// configurePlugin is already defined at the top of the script - no need to redefine


// Helper function to get the full property object from schema
// Uses greedy longest-match to handle schema keys containing dots (e.g., "eng.1")
function getSchemaProperty(schema, path) {
    if (!schema || !schema.properties) return null;

    const parts = path.split('.');
    let current = schema.properties;
    let i = 0;

    while (i < parts.length) {
        let matched = false;
        // Try progressively longer candidates, longest first
        for (let j = parts.length; j > i; j--) {
            const candidate = parts.slice(i, j).join('.');
            if (current && current[candidate]) {
                if (j === parts.length) {
                    // Consumed all remaining parts — done
                    return current[candidate];
                }
                if (current[candidate].properties) {
                    current = current[candidate].properties;
                    i = j;
                    matched = true;
                    break;
                } else {
                    return null; // Can't navigate deeper
                }
            }
        }
        if (!matched) {
            return null;
        }
    }

    return null;
}

// "x-display": "hidden" (or an object of nothing but hidden children).
// Mirrors prop_is_hidden in plugin_config.html.
function isHiddenSchemaProp(schema) {
    if (!schema || typeof schema !== 'object') return false;
    if (schema['x-display'] === 'hidden') return true;
    const children = schema.properties;
    if (children && typeof children === 'object') {
        const values = Object.values(children);
        return values.length > 0 && values.every(isHiddenSchemaProp);
    }
    return false;
}

// Helper function to render a single item in an array of objects
function renderArrayObjectItem(fieldId, fullKey, itemProperties, itemValue, index, itemsSchema) {
    const item = itemValue || {};
    const itemId = `${escapeAttribute(fieldId)}_item_${index}`;
    // Store original item data in data attribute to preserve non-editable properties after reindexing
    const itemDataJson = JSON.stringify(item);
    const itemDataBase64 = btoa(unescape(encodeURIComponent(itemDataJson)));
    let html = `<div id="${itemId}" class="border border-gray-300 rounded-lg p-4 bg-gray-50 array-object-item" data-index="${index}" data-item-data="${escapeAttribute(itemDataBase64)}">`;

    // Render each property of the object
    const propertyOrder = itemsSchema['x-propertyOrder'] || Object.keys(itemProperties);
    propertyOrder.forEach(propKey => {
        if (!itemProperties[propKey]) return;
        // "x-display": "hidden": no control. The stored value survives through
        // data-item-data, which updateArrayObjectData overlays edits onto.
        if (isHiddenSchemaProp(itemProperties[propKey])) return;

        const propSchema = itemProperties[propKey];
        const propValue = item[propKey] !== undefined ? item[propKey] : propSchema.default;
        const propLabel = propSchema.title || propKey.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        const propDescription = propSchema.description || '';
        const propFullKey = `${fullKey}[${index}].${propKey}`;

        html += `<div class="mb-3">`;

        // Handle file-upload widget (for logo field)
        if (propSchema['x-widget'] === 'file-upload') {
            html += `<label class="block text-sm font-medium text-gray-700 mb-1">${escapeHtml(propLabel)}</label>`;
            if (propDescription) {
                html += `<p class="text-xs text-gray-500 mb-2">${escapeHtml(propDescription)}</p>`;
            }
            const uploadConfig = propSchema['x-upload-config'] || {};
            // Derive pluginId strictly from uploadConfig or currentPluginConfig, no hard-coded fallback
            const pluginId = uploadConfig.plugin_id || (typeof currentPluginConfig !== 'undefined' ? currentPluginConfig?.pluginId : null) || (typeof window.currentPluginConfig !== 'undefined' ? window.currentPluginConfig?.pluginId : null) || null;
            const logoValue = propValue || {};
            // Use base64 encoding for JSON in data attributes to safely handle all characters
            const logoDataJson = logoValue && Object.keys(logoValue).length > 0 ? JSON.stringify(logoValue) : '';
            const logoDataBase64 = logoDataJson ? btoa(unescape(encodeURIComponent(logoDataJson))) : '';
            const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/bmp'];
            const maxSizeMB = uploadConfig.max_size_mb || 5;
            const pluginIdParam = pluginId ? `'${escapeAttribute(pluginId)}'` : 'null';
            const uploadConfigJson = JSON.stringify({ allowed_types: allowedTypes, max_size_mb: maxSizeMB });
            const uploadConfigBase64 = btoa(unescape(encodeURIComponent(uploadConfigJson)));

            html += `
                <div class="file-upload-widget-inline"${logoDataBase64 ? ` data-file-data="${escapeAttribute(logoDataBase64)}" data-prop-key="${escapeAttribute(propKey)}"` : ` data-prop-key="${escapeAttribute(propKey)}"`} data-upload-config="${escapeAttribute(uploadConfigBase64)}">
                    <input type="file"
                           id="${escapeAttribute(itemId)}_logo_file"
                           accept="${escapeAttribute(allowedTypes.join(','))}"
                           style="display: none;"
                           onchange="handleArrayObjectFileUpload(event, '${escapeAttribute(fieldId)}', ${index}, '${escapeAttribute(propKey)}', ${pluginIdParam})">
                    <button type="button"
                            onclick="document.getElementById('${escapeAttribute(itemId)}_logo_file').click()"
                            class="px-3 py-2 text-sm bg-gray-200 hover:bg-gray-300 text-gray-700 rounded-md transition-colors">
                        <i class="fas fa-upload mr-1"></i> Upload Logo
                    </button>
            `;

            if (logoValue.path) {
                html += `
                    <div class="mt-2 flex items-center space-x-2 uploaded-image-container">
                        <img src="/${escapeAttribute(logoValue.path.replace(/^\/+/, ''))}" alt="Logo" loading="lazy" decoding="async" class="w-16 h-16 object-cover rounded border">
                        <button type="button"
                                onclick="removeArrayObjectFile('${escapeAttribute(fieldId)}', ${index}, '${escapeAttribute(propKey)}')"
                                class="text-red-600 hover:text-red-800">
                            <i class="fas fa-trash"></i> Remove
                        </button>
                    </div>
                `;
            }

            html += `</div>`;
        } else if (propSchema.type === 'boolean') {
            // Boolean checkbox
            html += `
                <label class="flex items-center">
                    <input type="checkbox"
                           id="${escapeAttribute(itemId)}_${escapeAttribute(propKey)}"
                           data-prop-key="${escapeAttribute(propKey)}"
                           class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                           ${propValue ? 'checked' : ''}
                           onchange="updateArrayObjectData('${escapeAttribute(fieldId)}')">
                    <span class="ml-2 text-sm text-gray-700">${escapeHtml(propLabel)}</span>
                </label>
            `;
        } else {
            // Regular text/string input
            html += `
                <label for="${escapeAttribute(itemId)}_${escapeAttribute(propKey)}" class="block text-sm font-medium text-gray-700 mb-1">
                    ${escapeHtml(propLabel)}
                </label>
            `;
            if (propDescription) {
                html += `<p class="text-xs text-gray-500 mb-1">${escapeHtml(propDescription)}</p>`;
            }
            const placeholder = propSchema.format === 'uri' ? 'https://example.com/feed' : '';
            html += `
                <input type="${propSchema.format === 'uri' ? 'url' : 'text'}"
                       id="${escapeAttribute(itemId)}_${escapeAttribute(propKey)}"
                       data-prop-key="${escapeAttribute(propKey)}"
                       class="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm bg-white text-black"
                       value="${escapeAttribute(propValue || '')}"
                       placeholder="${escapeAttribute(placeholder)}"
                       onchange="updateArrayObjectData('${escapeAttribute(fieldId)}')">
            `;
        }

        html += `</div>`;
    });

    // Use schema-driven label for remove button, fallback to generic "Remove item"
    const removeLabel = itemsSchema['x-removeLabel'] || 'Remove item';
    html += `
        <button type="button"
                onclick="removeArrayObjectItem('${escapeAttribute(fieldId)}', ${index})"
                class="mt-2 px-3 py-2 text-sm text-red-600 hover:text-red-800 hover:bg-red-50 rounded-md transition-colors">
            <i class="fas fa-trash mr-1"></i> ${escapeHtml(removeLabel)}
        </button>
    </div>`;

    return html;
}


// Functions to handle patternProperties key-value pairs
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

window.updateArrayObjectData = function(fieldId) {
    const itemsContainer = document.getElementById(fieldId + '_items');
    const hiddenInput = document.getElementById(fieldId + '_data');
    if (!itemsContainer || !hiddenInput) return;

    // Get existing items from hidden input to preserve non-editable properties
    let existingItems = [];
    try {
        const existingData = hiddenInput.value.trim();
        if (existingData) {
            existingItems = JSON.parse(existingData);
        }
    } catch (e) {
        console.error('Error parsing existing items data:', e);
    }

    const items = [];
    const itemElements = itemsContainer.querySelectorAll('.array-object-item');

    itemElements.forEach((itemEl, index) => {
        // Start with original item data from data attribute to preserve non-editable properties
        // This avoids index-based corruption after deletions/reindexing
        let existingItem = {};
        const itemDataBase64 = itemEl.getAttribute('data-item-data');
        if (itemDataBase64) {
            try {
                const itemDataJson = decodeURIComponent(escape(atob(itemDataBase64)));
                existingItem = JSON.parse(itemDataJson);
            } catch (e) {
                console.error('Error parsing item data from data attribute:', e);
                // Fallback to index-based lookup if data attribute is missing/corrupt
                if (index < existingItems.length && existingItems[index]) {
                    existingItem = existingItems[index];
                }
            }
        } else {
            // Fallback to index-based lookup if data attribute is missing
            if (index < existingItems.length && existingItems[index]) {
                existingItem = existingItems[index];
            }
        }
        const item = Object.assign({}, existingItem); // Copy existing item

        // Get all text inputs in this item and overlay their values with type coercion
        itemEl.querySelectorAll('input[type="text"], input[type="url"], input[type="number"]').forEach(input => {
            const propKey = input.getAttribute('data-prop-key');
            if (propKey && propKey !== 'logo_file') {
                let value = input.value.trim();

                // Type coercion: check input type or data-prop-type attribute
                const inputType = input.type;
                const propType = input.getAttribute('data-prop-type');

                if (inputType === 'number' || propType === 'number') {
                    // Use valueAsNumber if available, fallback to Number()
                    const numValue = input.valueAsNumber !== undefined && !isNaN(input.valueAsNumber)
                        ? input.valueAsNumber
                        : Number(value);
                    item[propKey] = isNaN(numValue) ? value : numValue;
                } else if (propType === 'array' || input.getAttribute('data-prop-is-list') === 'true') {
                    // Try to parse as JSON array, fallback to comma splitting
                    try {
                        const parsed = JSON.parse(value);
                        item[propKey] = Array.isArray(parsed) ? parsed : value;
                    } catch (e) {
                        // Fallback to comma-splitting for arrays
                        item[propKey] = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                    }
                } else {
                    // String value - keep as-is
                    item[propKey] = value;
                }
            }
        });
        // Handle checkboxes
        itemEl.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
            const propKey = checkbox.getAttribute('data-prop-key');
            if (propKey) {
                item[propKey] = checkbox.checked;
            }
        });
        // Handle file upload data (stored in data attributes, base64-encoded)
        itemEl.querySelectorAll('[data-file-data]').forEach(fileEl => {
            const fileDataBase64 = fileEl.getAttribute('data-file-data');
            if (fileDataBase64) {
                try {
                    // Decode base64-encoded JSON
                    const fileDataJson = decodeURIComponent(escape(atob(fileDataBase64)));
                    const data = JSON.parse(fileDataJson);
                    const propKey = fileEl.getAttribute('data-prop-key');
                    if (propKey) {
                        item[propKey] = data;
                    }
                } catch (e) {
                    console.error('Error parsing file data:', e);
                }
            }
        });
        items.push(item);

        // Update data-item-data attribute with the merged item to keep it in sync
        try {
            const itemDataJson = JSON.stringify(item);
            const itemDataBase64 = btoa(unescape(encodeURIComponent(itemDataJson)));
            itemEl.setAttribute('data-item-data', itemDataBase64);
        } catch (e) {
            console.error('Error updating data-item-data attribute:', e);
        }
    });

    hiddenInput.value = JSON.stringify(items);
};

window.handleArrayObjectFileUpload = async function(event, fieldId, itemIndex, propKey, pluginId) {
    const file = event.target.files[0];
    if (!file) return;

    // Derive item element from event instead of constructing ID (works after reindexing)
    const itemEl = event.target.closest('.array-object-item');
    if (!itemEl) {
        console.error('Array object item element not found');
        return;
    }

    // Find file upload container within the item element, scoped to propKey
    const fileUploadContainer = itemEl.querySelector(`.file-upload-widget-inline[data-prop-key="${propKey}"]`);
    if (!fileUploadContainer) {
        console.error('File upload container not found for propKey:', propKey);
        return;
    }

    // Get upload config from data attribute
    let uploadConfig = { allowed_types: ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp'], max_size_mb: 5 };
    const uploadConfigBase64 = fileUploadContainer.getAttribute('data-upload-config');
    if (uploadConfigBase64) {
        try {
            const uploadConfigJson = decodeURIComponent(escape(atob(uploadConfigBase64)));
            uploadConfig = JSON.parse(uploadConfigJson);
        } catch (e) {
            console.error('Error parsing upload config from data attribute:', e);
        }
    }

    // Validate file type using uploadConfig
    const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp'];
    if (!allowedTypes.includes(file.type)) {
        showNotification(`File ${file.name} is not a valid image type`, 'error');
        return;
    }

    // Validate file size using uploadConfig
    const maxSizeMB = uploadConfig.max_size_mb || 5;
    if (file.size > maxSizeMB * 1024 * 1024) {
        showNotification(`File ${file.name} exceeds ${maxSizeMB}MB limit`, 'error');
        return;
    }

    // Validate pluginId before upload (fail fast)
    if (!pluginId || pluginId === 'null' || pluginId === 'undefined' || (typeof pluginId === 'string' && pluginId.trim() === '')) {
        showNotification('Plugin ID is required for file upload', 'error');
        console.error('File upload failed: pluginId is required');
        return;
    }

    // Upload file
    const formData = new FormData();
    formData.append('plugin_id', pluginId);
    formData.append('files', file);

    try {
        const response = await fetch('/api/v3/plugins/assets/upload', {
            method: 'POST',
            body: formData
        });

        // Check response.ok before parsing JSON to avoid parsing errors on HTTP errors
        if (!response.ok) {
            const errorText = await response.text();
            let errorMessage = `Upload failed: HTTP ${response.status}`;
            try {
                const errorData = JSON.parse(errorText);
                errorMessage = errorData.message || errorMessage;
            } catch (e) {
                // If response isn't JSON, use the text or status
                if (errorText) {
                    errorMessage = `Upload failed: ${errorText}`;
                }
            }
            showNotification(errorMessage, 'error');
            return;
        }

        const data = await response.json();

        if (data.status === 'success' && data.uploaded_files && data.uploaded_files.length > 0) {
            const uploadedFile = data.uploaded_files[0];

            // Store file data in data-file-data attribute on the container (base64-encoded)
            const fileDataJson = JSON.stringify(uploadedFile);
            const fileDataBase64 = btoa(unescape(encodeURIComponent(fileDataJson)));
            fileUploadContainer.setAttribute('data-file-data', fileDataBase64);
            fileUploadContainer.setAttribute('data-prop-key', propKey);

            // Update the display to show the uploaded image
            const existingImage = fileUploadContainer.querySelector('.uploaded-image-container');
            if (existingImage) {
                existingImage.remove();
            }

            const imageContainer = document.createElement('div');
            imageContainer.className = 'mt-2 flex items-center space-x-2 uploaded-image-container';
            const escapedPath = escapeAttribute(uploadedFile.path.replace(/^\/+/, ''));
            const escapedFieldId = escapeAttribute(fieldId);
            const escapedPropKey = escapeAttribute(propKey);
            // Get current item index from data-index attribute for remove button
            const currentItemIndex = itemEl.getAttribute('data-index') || itemIndex;
            imageContainer.innerHTML = `
                <img src="/${escapedPath}" alt="Logo" loading="lazy" decoding="async" class="w-16 h-16 object-cover rounded border">
                <button type="button"
                        onclick="removeArrayObjectFile('${escapedFieldId}', ${currentItemIndex}, '${escapedPropKey}')"
                        class="text-red-600 hover:text-red-800">
                    <i class="fas fa-trash"></i> Remove
                </button>
            `;
            fileUploadContainer.appendChild(imageContainer);

            // Update the hidden input with the new file data
            updateArrayObjectData(fieldId);

            showNotification('Logo uploaded successfully', 'success');
        } else {
            showNotification(`Upload failed: ${data.message || 'Unknown error'}`, 'error');
        }
    } catch (error) {
        console.error('Upload error:', error);
        showNotification(`Upload error: ${error.message}`, 'error');
    }

    // Clear file input
    event.target.value = '';
};

window.removeArrayObjectFile = function(fieldId, itemIndex, propKey) {
    const itemId = `${fieldId}_item_${itemIndex}`;
    const fileUploadContainer = document.querySelector(`#${itemId} .file-upload-widget-inline`);
    if (!fileUploadContainer) {
        console.error('File upload container not found');
        return;
    }

    // Remove file data from data attribute
    fileUploadContainer.removeAttribute('data-file-data');

    // Remove the image display
    const imageContainer = fileUploadContainer.querySelector('.uploaded-image-container');
    if (imageContainer) {
        imageContainer.remove();
    }

    // Update the hidden input to remove the file data
    updateArrayObjectData(fieldId);

    showNotification('Logo removed', 'success');
};

// Generic Plugin Action Handler
window.executePluginAction = function(actionId, actionIndex, pluginIdParam = null) {
    debugLog('[DEBUG] executePluginAction called - actionId:', actionId, 'actionIndex:', actionIndex, 'pluginIdParam:', pluginIdParam);

    // Construct button ID first (we have actionId and actionIndex)
    const actionIdFull = `action-${actionId}-${actionIndex}`;
    const statusId = `action-status-${actionId}-${actionIndex}`;
    const btn = document.getElementById(actionIdFull);
    const statusDiv = document.getElementById(statusId);

    // Get plugin ID from multiple sources with comprehensive fallback logic
    let pluginId = pluginIdParam;

    // Fallback 1: Try to get from button's data-plugin-id attribute
    if (!pluginId && btn) {
        pluginId = btn.getAttribute('data-plugin-id');
        if (pluginId) {
            debugLog('[DEBUG] Got pluginId from button data attribute:', pluginId);
        }
    }

    // Fallback 2: Try to get from closest parent with data-plugin-id
    if (!pluginId && btn) {
        const parentWithPluginId = btn.closest('[data-plugin-id]');
        if (parentWithPluginId) {
            pluginId = parentWithPluginId.getAttribute('data-plugin-id');
            if (pluginId) {
                debugLog('[DEBUG] Got pluginId from parent element:', pluginId);
            }
        }
    }

    // Fallback 3: Try to get from plugin-config-container or plugin-config-tab
    if (!pluginId && btn) {
        const container = btn.closest('.plugin-config-container, .plugin-config-tab, [id^="plugin-config-"]');
        if (container) {
            // Try data-plugin-id first
            pluginId = container.getAttribute('data-plugin-id');
            if (!pluginId) {
                // Try to extract from ID like "plugin-config-{pluginId}"
                const idMatch = container.id.match(/plugin-config-(.+)/);
                if (idMatch) {
                    pluginId = idMatch[1];
                }
            }
            if (pluginId) {
                debugLog('[DEBUG] Got pluginId from container:', pluginId);
            }
        }
    }

    // Fallback 4: Try to get from currentPluginConfig
    if (!pluginId) {
        pluginId = currentPluginConfig?.pluginId;
        if (pluginId) {
            debugLog('[DEBUG] Got pluginId from currentPluginConfig:', pluginId);
        }
    }

    // Fallback 5: the active tab, when it is a plugin's tab
    if (!pluginId) {
        const appData = window.getApp();
        if (appData && appData.activeTab && appData.activeTab !== 'overview' && appData.activeTab !== 'plugins' && appData.activeTab !== 'wifi') {
            pluginId = appData.activeTab;
            debugLog('[DEBUG] Got pluginId from Alpine activeTab:', pluginId);
        }
    }

    // Fallback 6: Try to find from plugin tab elements (scoped to button context)
    if (!pluginId && btn) {
        try {
            // Search within the button's Alpine.js context (closest x-data element)
            const buttonContext = btn.closest('[x-data]');
            if (buttonContext) {
                const pluginTab = buttonContext.querySelector('[x-show*="activeTab === plugin.id"]');
                if (pluginTab && window.Alpine) {
                    try {
                        const pluginData = Alpine.$data(buttonContext);
                        if (pluginData && pluginData.plugin) {
                            pluginId = pluginData.plugin.id;
                            if (pluginId) {
                                debugLog('[DEBUG] Got pluginId from Alpine plugin data (scoped to button context):', pluginId);
                            }
                        }
                    } catch (e) {
                        console.warn('[DEBUG] Error accessing Alpine plugin data:', e);
                    }
                }
            }
            // If not found in button context, try container element
            if (!pluginId) {
                const container = btn.closest('.plugin-config-container, .plugin-config-tab, [id^="plugin-config-"]');
                if (container) {
                    const containerContext = container.querySelector('[x-show*="activeTab === plugin.id"]');
                    if (containerContext && window.Alpine) {
                        try {
                            const containerData = Alpine.$data(container.closest('[x-data]'));
                            if (containerData && containerData.plugin) {
                                pluginId = containerData.plugin.id;
                                if (pluginId) {
                                    debugLog('[DEBUG] Got pluginId from Alpine plugin data (scoped to container):', pluginId);
                                }
                            }
                        } catch (e) {
                            console.warn('[DEBUG] Error accessing Alpine plugin data from container:', e);
                        }
                    }
                }
            }
        } catch (e) {
            console.warn('[DEBUG] Error in fallback 6 DOM lookup:', e);
        }
    }

    // Final check - if still no pluginId, show error
    if (!pluginId) {
        console.error('No plugin ID available after all fallbacks. actionId:', actionId, 'actionIndex:', actionIndex);
        console.error('[DEBUG] Button found:', !!btn);
        console.error('[DEBUG] currentPluginConfig:', currentPluginConfig);
        showNotification('Unable to determine plugin ID. Please refresh the page.', 'error');
        return;
    }

    debugLog('[DEBUG] executePluginAction - Final pluginId:', pluginId, 'actionId:', actionId, 'actionIndex:', actionIndex);

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
        debugLog('[DEBUG] currentPluginConfig:', currentPluginConfig);
        debugLog('[DEBUG] installedPlugins:', window.installedPlugins);
        showNotification(`Action ${actionId} not found. Please refresh the page.`, 'error');
        return;
    }

    debugLog('[DEBUG] Found action:', action);

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
                statusDiv.innerHTML = `<div class="text-green-600"><i class="fas fa-check-circle mr-2"></i>${escapeHtml(data.message || 'Action completed successfully')}</div>`;
                btn.innerHTML = originalText;
                btn.disabled = false;
                delete btn.dataset.step;
                showNotification(data.message || 'Action completed successfully!', 'success');
            } else {
                statusDiv.innerHTML = `<div class="text-red-600"><i class="fas fa-exclamation-circle mr-2"></i>${escapeHtml(data.message || 'Error')}</div>`;
                if (data.output) {
                    statusDiv.innerHTML += `<pre class="mt-2 text-xs bg-red-50 p-2 rounded overflow-auto max-h-32">${escapeHtml(data.output)}</pre>`;
                }
                btn.innerHTML = originalText;
                btn.disabled = false;
                delete btn.dataset.step;
            }
        })
        .catch(error => {
            statusDiv.innerHTML = `<div class="text-red-600"><i class="fas fa-exclamation-circle mr-2"></i>Error: ${escapeHtml(error.message)}</div>`;
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
                            <i class="fas fa-link mr-2"></i>${escapeHtml(data.message || 'Authorization URL Generated')}
                        </div>
                        <div class="mb-3">
                            <p class="text-sm text-blue-700 mb-2">1. Click the link below to authorize:</p>
                            <a href="${data.auth_url && data.auth_url.startsWith('http') ? escapeHtml(data.auth_url) : '#'}" target="_blank" class="text-blue-600 hover:text-blue-800 underline break-all">
                                ${escapeHtml(data.auth_url || '')}
                            </a>
                        </div>
                        <div class="mb-2">
                            <p class="text-sm text-blue-700 mb-2">2. After authorization, copy the FULL redirect URL from your browser.</p>
                            <p class="text-sm text-blue-600">3. Click the button again and paste the redirect URL when prompted.</p>
                        </div>
                    </div>
                `;
                btn.textContent = action.step2_button_text || 'Complete Authentication';
                btn.dataset.step = '2';
                btn.disabled = false;
                showNotification(data.message || 'Authorization URL generated. Please authorize and paste the redirect URL.', 'info');
            } else {
                // Simple success
                statusDiv.innerHTML = `
                    <div class="bg-green-50 border border-green-200 rounded p-3">
                        <div class="text-green-900 font-medium mb-2">
                            <i class="fas fa-check-circle mr-2"></i>${escapeHtml(data.message || 'Action completed successfully')}
                        </div>
                        ${data.output ? `<pre class="mt-2 text-xs bg-green-50 p-2 rounded overflow-auto max-h-32">${escapeHtml(data.output)}</pre>` : ''}
                    </div>
                `;
                btn.innerHTML = originalText;
                btn.disabled = false;
                showNotification(data.message || 'Action completed successfully!', 'success');
            }
        } else {
            statusDiv.innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded p-3">
                    <div class="text-red-900 font-medium mb-2">
                        <i class="fas fa-exclamation-circle mr-2"></i>${escapeHtml(data.message || 'Action failed')}
                    </div>
                    ${data.output ? `<pre class="mt-2 text-xs bg-red-50 p-2 rounded overflow-auto max-h-32">${escapeHtml(data.output)}</pre>` : ''}
                </div>
            `;
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    })
    .catch(error => {
        statusDiv.innerHTML = `<div class="text-red-600"><i class="fas fa-exclamation-circle mr-2"></i>Error: ${escapeHtml(error.message)}</div>`;
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}

// togglePlugin is already defined at the top of the script - no need to redefine

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
        debugLog('Uninstall response:', data);

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

function pollOperationStatus(operationId, pluginId, pluginName, options = {}) {
    const maxAttempts = options.maxAttempts || 60;
    const attempt = options.attempt || 0;
    const onComplete = options.onComplete || (() => handleUninstallSuccess(pluginId));
    const onFailed = options.onFailed || ((errorMsg) => {
        showNotification(errorMsg || `Operation failed for ${pluginName}`, 'error');
        setTimeout(() => loadInstalledPlugins(), 1000);
    });
    const onTimeout = options.onTimeout || (() => {
        showNotification(`Operation timed out for ${pluginName}`, 'error');
        setTimeout(() => loadInstalledPlugins(), 1000);
    });

    if (attempt >= maxAttempts) {
        onTimeout();
        return;
    }

    const pollAgain = () => setTimeout(() => {
        pollOperationStatus(operationId, pluginId, pluginName, { ...options, attempt: attempt + 1 });
    }, 1000);

    fetch(`/api/v3/plugins/operation/${operationId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success' && data.data) {
                const operation = data.data;
                const status = operation.status;

                if (status === 'completed') {
                    onComplete();
                } else if (status === 'failed') {
                    onFailed(operation.error || operation.message);
                } else {
                    // 'pending', 'in_progress', or unknown - poll again
                    pollAgain();
                }
            } else {
                // Error getting operation status, try again
                pollAgain();
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
    installedPlugins = updatedPlugins;
    renderInstalledPlugins(updatedPlugins);
    showNotification(`Plugin uninstalled successfully`, 'success');

    // Also refresh from server to ensure consistency
    setTimeout(() => {
        loadInstalledPlugins();
    }, 1000);
}

function refreshPlugins() {
    debugLog('[refreshPlugins] Button clicked, refreshing plugins...');
    // Clear cache to force fresh data
    pluginStoreCache = null;
    cacheTimestamp = null;

    // refreshInstalledPlugins() is async (returns a Promise via loadInstalledPlugins).
    // Only search the store and notify after window.installedPlugins is updated so
    // that Installed/Reinstall badges reflect the freshly fetched state.
    refreshInstalledPlugins().then(() => {
        searchPluginStore(true);
        showNotification('Plugins refreshed with latest metadata from GitHub', 'success');
    });
}

function restartDisplay() {
    debugLog('[restartDisplay] Button clicked, restarting display service...');
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
    pluginLog('[STORE] Searching plugin store...', { fetchCommitInfo });

    const now = Date.now();
    const isCacheValid = pluginStoreCache && cacheTimestamp && (now - cacheTimestamp < CACHE_DURATION);

    // If cache is valid and we don't need fresh commit info, just re-filter
    if (isCacheValid && !fetchCommitInfo) {
        debugLog('Using cached plugin store data');
        const storeGrid = document.getElementById('plugin-store-grid');
        if (storeGrid) {
            applyStoreFiltersAndSort();
            return;
        }
    }

    // Show loading state
    try {
        const countEl = document.getElementById('store-count');
        if (countEl) countEl.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Loading...';
    } catch (e) { /* ignore */ }
    showStoreLoading(true);

    let url = '/api/v3/plugins/store/list';
    if (!fetchCommitInfo) {
        url += '?fetch_commit_info=false';
    }

    debugLog('Store URL:', url);

    fetch(url)
        .then(response => response.json())
        .then(data => {
            showStoreLoading(false);

            if (data.status === 'success') {
                const plugins = data.data.plugins || [];
                debugLog('Store plugins count:', plugins.length);

                pluginStoreCache = plugins;
                cacheTimestamp = Date.now();

                const storeGrid = document.getElementById('plugin-store-grid');
                if (!storeGrid) {
                    pluginLog('[STORE] plugin-store-grid not ready, deferring render');
                    window.__pendingStorePlugins = plugins;
                    return;
                }

                // Update total count
                try {
                    const countEl = document.getElementById('store-count');
                    if (countEl) countEl.innerHTML = `${plugins.length} available`;
                } catch (e) { /* ignore */ }

                applyStoreFiltersAndSort();

                // Re-attach GitHub token collapse handler after store render
                if (window.attachGithubTokenCollapseHandler) {
                    requestAnimationFrame(() => {
                        try { window.attachGithubTokenCollapseHandler(); } catch (e) { /* ignore */ }
                        if (window.checkGitHubAuthStatus) {
                            try { window.checkGitHubAuthStatus(); } catch (e) { /* ignore */ }
                        }
                    });
                }
            } else {
                showNotification('Failed to search plugin store: ' + data.message, 'error');
                try {
                    const countEl = document.getElementById('store-count');
                    if (countEl) countEl.innerHTML = 'Error loading';
                } catch (e) { /* ignore */ }
            }
        })
        .catch(error => {
            console.error('Error searching plugin store:', error);
            showStoreLoading(false);
            showNotification('Error searching plugin store: ' + error.message, 'error');
            try {
                const countEl = document.getElementById('store-count');
                if (countEl) countEl.innerHTML = 'Error loading';
            } catch (e) { /* ignore */ }
        });
}

function showStoreLoading(show) {
    const loading = document.querySelector('.store-loading');
    if (loading) {
        loading.style.display = show ? 'block' : 'none';
    }
}

// ── Plugin Store: Client-Side Filter/Sort/Pagination ────────────────────────

function isStorePluginInstalled(pluginIdOrPlugin) {
    const installed = window.installedPlugins || installedPlugins || [];
    // Accept either a plain ID string or a store plugin object (which may have plugin_path)
    if (typeof pluginIdOrPlugin === 'string') {
        return installed.some(p => p.id === pluginIdOrPlugin);
    }
    const storeId = pluginIdOrPlugin.id;
    // Derive the actual installed directory name from plugin_path (e.g. "plugins/ledmatrix-weather" → "ledmatrix-weather")
    const pluginPath = pluginIdOrPlugin.plugin_path || '';
    const pathDerivedId = pluginPath ? pluginPath.split('/').pop() : null;
    return installed.some(p => p.id === storeId || (pathDerivedId && p.id === pathDerivedId));
}

// ── Plugin Store: search / filter / sort ────────────────────────────────
// Behaviour, element ids and localStorage keys are unchanged from the
// hand-rolled version this replaces — only the machinery is now shared.
const STORE_COMPARATORS = {
    'a-z': (a, b) => storeSortName(a).localeCompare(storeSortName(b)),
    'z-a': (a, b) => storeSortName(b).localeCompare(storeSortName(a)),
    'category': (a, b) => {
        const catCmp = (a.category || '').localeCompare(b.category || '');
        return catCmp !== 0 ? catCmp : storeSortName(a).localeCompare(storeSortName(b));
    },
    'author': (a, b) => {
        const authCmp = (a.author || '').localeCompare(b.author || '');
        return authCmp !== 0 ? authCmp : storeSortName(a).localeCompare(storeSortName(b));
    },
    'newest': (a, b) => {
        // Missing dates count as epoch 0, so they land last under a descending
        // sort — same as before.
        const dateA = a.last_updated ? new Date(a.last_updated).getTime() : 0;
        const dateB = b.last_updated ? new Date(b.last_updated).getTime() : 0;
        return dateB - dateA; // newest first
    },
};

function storeSortName(plugin) {
    return (plugin.name || plugin.id || '').toLowerCase();
}

let _storeFilter = null;

function getStoreFilter() {
    if (_storeFilter) return _storeFilter;
    if (!window.ListFilter || typeof window.ListFilter.create !== 'function') {
        console.warn('[PLUGINS] ListFilter helper unavailable — store filters disabled');
        return null;
    }
    _storeFilter = window.ListFilter.create({
        getItems: () => pluginStoreCache || [],
        render: renderPluginStore,
        search: {
            el: 'plugin-search',
            fields: ['name', 'description', 'author', 'id', 'category', 'tags'],
            debounceMs: 150,
        },
        sort: {
            el: 'store-sort',
            default: 'a-z',
            comparators: STORE_COMPARATORS,
        },
        controls: [
            {
                type: 'select',
                el: 'plugin-category',
                key: 'filterCategory',
                default: '',
                test: (plugin, value) => (plugin.category || '').toLowerCase() === value.toLowerCase(),
            },
            {
                // Cycles All → Installed → Not Installed → All.
                type: 'cycle',
                el: 'store-filter-installed',
                key: 'filterInstalled',
                default: null,
                values: [null, true, false],
                test: (plugin, value) => (value === true ? isStorePluginInstalled(plugin) : !isStorePluginInstalled(plugin)),
                render: (btn, value) => {
                    if (value === true) {
                        btn.innerHTML = '<i class="fas fa-check-circle mr-1 text-green-500"></i>Installed';
                        btn.classList.add('border-green-400', 'bg-green-50');
                        btn.classList.remove('border-gray-300', 'bg-white', 'border-red-400', 'bg-red-50');
                    } else if (value === false) {
                        btn.innerHTML = '<i class="fas fa-times-circle mr-1 text-red-500"></i>Not Installed';
                        btn.classList.add('border-red-400', 'bg-red-50');
                        btn.classList.remove('border-gray-300', 'bg-white', 'border-green-400', 'bg-green-50');
                    } else {
                        btn.innerHTML = '<i class="fas fa-filter mr-1 text-gray-400"></i>All';
                        btn.classList.add('border-gray-300', 'bg-white');
                        btn.classList.remove('border-green-400', 'bg-green-50', 'border-red-400', 'bg-red-50');
                    }
                },
            },
        ],
        pagination: {
            perPageEl: 'store-per-page',
            defaultPerPage: 12,
            topEl: 'store-pagination-top',
            bottomEl: 'store-pagination-bottom',
            infoEl: 'store-results-info',
            infoBottomEl: 'store-results-info-bottom',
            scrollToEl: 'plugin-store-grid',
            infoFormat: (start, end, total) => `Showing ${start}\u2013${end} of ${total} plugins`,
            emptyText: 'No plugins match your filters',
        },
        persist: {
            read: () => ({
                sort: safeLocalStorage.getItem('storeSort') || undefined,
                perPage: parseInt(safeLocalStorage.getItem('storePerPage')) || undefined,
            }),
            write: (state) => {
                safeLocalStorage.setItem('storeSort', state.sort);
                safeLocalStorage.setItem('storePerPage', state.perPage);
            },
        },
        clearEl: 'store-clear-filters',
        activeCountEl: 'store-active-filters',
    });
    return _storeFilter;
}

function applyStoreFiltersAndSort(skipPageReset) {
    if (!pluginStoreCache) return;
    const ctl = getStoreFilter();
    if (ctl) {
        ctl.apply(skipPageReset);
        return;
    }
    // Fallback: no helper, render the cache unfiltered rather than nothing.
    renderPluginStore(pluginStoreCache);
}

function setupStoreFilterListeners() {
    const ctl = getStoreFilter();
    if (!ctl) return;
    ctl.bind();
    ctl.syncControls();
}

// Expose searchPluginStore on window.pluginManager for Alpine.js integration
window.searchPluginStore = searchPluginStore;
window.pluginManager.searchPluginStore = searchPluginStore;

function renderPluginStore(plugins) {
    const container = document.getElementById('plugin-store-grid');
    if (!container) {
        pluginLog('[RENDER] plugin-store-grid not yet available, deferring render');
        window.__pendingStorePlugins = plugins;
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


    setGridHtmlIfChanged(container, plugins.map(plugin => {
        const installed = isStorePluginInstalled(plugin);
        // Registry data: only open real web links, never javascript: URLs.
        const repoLink = plugin.repo && /^https?:\/\//i.test(plugin.repo) ? plugin.repo : '';
        return `
        <div class="plugin-card">
            <div class="flex items-start justify-between mb-4">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center flex-wrap gap-1.5 mb-2">
                        <h4 class="font-semibold text-gray-900 text-base">${escapeHtml(plugin.name || plugin.id)}</h4>
                        ${plugin.verified ? '<span class="badge badge-success"><i class="fas fa-check-circle mr-1"></i>Verified</span>' : ''}
                        ${installed ? '<span class="badge badge-success"><i class="fas fa-check mr-1"></i>Installed</span>' : ''}
                        ${isNewPlugin(plugin.last_updated) ? '<span class="badge badge-info"><i class="fas fa-sparkles mr-1"></i>New</span>' : ''}
                        ${plugin._source === 'custom_repository' ? `<span class="badge badge-accent" title="From: ${escapeHtml(plugin._repository_name || plugin._repository_url || 'Custom Repository')}"><i class="fas fa-bookmark mr-1"></i>Custom</span>` : ''}
                    </div>
                    <div class="text-sm text-gray-600 space-y-1.5 mb-3">
                        <p class="flex items-center"><i class="fas fa-user mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.author || 'Unknown')}</p>
                        ${plugin.version ? `<p class="flex items-center"><i class="fas fa-tag mr-2 text-gray-400 w-4"></i>v${escapeHtml(plugin.version)}</p>` : ''}
                        <p class="flex items-center"><i class="fas fa-folder mr-2 text-gray-400 w-4"></i>${escapeHtml(plugin.category || 'General')}</p>
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
            <div class="mt-auto pt-4 border-t border-gray-200 space-y-2">
                <div class="flex items-center gap-2">
                    <label for="branch-input-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}" class="text-xs text-gray-600 whitespace-nowrap">
                        <i class="fas fa-code-branch mr-1"></i>Branch:
                    </label>
                    <input type="text" id="branch-input-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}"
                           placeholder="main (default)"
                           class="flex-1 px-2 py-1 text-xs border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                <div class="flex gap-2">
                    <button onclick='if(window.installPlugin){const branchInput = document.getElementById("branch-input-${plugin.id.replace(/[^a-zA-Z0-9]/g, '-')}"); window.installPlugin(${jsStringAttr(plugin.id)}, branchInput?.value?.trim() || null)}else{console.error("installPlugin not available")}' class="btn ${installed ? 'bg-gray-500 hover:bg-gray-600' : 'bg-green-600 hover:bg-green-700'} text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold">
                        <i class="fas ${installed ? 'fa-redo' : 'fa-download'} mr-2"></i>${installed ? 'Reinstall' : 'Install'}
                    </button>
                    <button onclick='${repoLink ? `window.open(${jsStringAttr(plugin.plugin_path ? repoLink + "/tree/" + encodeURIComponent(plugin.default_branch || plugin.branch || "main") + "/" + plugin.plugin_path.split("/").map(encodeURIComponent).join("/") : repoLink)}, "_blank")` : `void(0)`}' ${repoLink ? '' : 'disabled'} class="btn bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md text-sm flex-1 font-semibold${repoLink ? '' : ' opacity-50 cursor-not-allowed'}">
                        <i class="fas fa-external-link-alt mr-2"></i>View
                    </button>
                </div>
            </div>
        </div>`;
    }).join(''));
}

// Expose functions to window for onclick handlers
window.installPlugin = function(pluginId, branch = null) {
    showNotification(`Installing ${pluginId}${branch ? ` (branch: ${branch})` : ''}...`, 'info');

    const requestBody = { plugin_id: pluginId };
    if (branch) {
        requestBody.branch = branch;
    }

    function enableAfterInstall() {
        // Enable immediately so install -> enable is one step; only nudge
        // for a restart once enablement actually succeeded (persistent
        // toast; duration 0 = stays until dismissed).
        Promise.resolve(window.togglePlugin(pluginId, true)).then(toggleResult => {
            if (toggleResult && toggleResult.status === 'success') {
                showNotification(
                    `${pluginId} installed and enabled — restart the display to show it`,
                    {
                        type: 'success',
                        duration: 0,
                        actionLabel: 'Restart Now',
                        onAction: () => restartDisplay()
                    }
                );
            } else {
                showNotification(
                    `${pluginId} installed, but enabling it failed — use its toggle in the plugin list`,
                    'warning'
                );
            }
        });
        // Refresh installed plugins list, then re-render store to update badges
        loadInstalledPlugins();
        setTimeout(() => applyStoreFiltersAndSort(true), 500);
    }

    fetch('/api/v3/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status !== 'success') return;

        if (data.data && data.data.operation_id) {
            // Install runs async via the operation queue - this response only
            // means "queued", not "installed". Enabling immediately here would
            // 404 with "Plugin not found" against the toggle endpoint, since
            // the plugin manager hasn't discovered the new plugin yet (seen
            // live: "installation queued" followed immediately by a failed
            // enable). Wait for the operation to actually finish first.
            pollOperationStatus(data.data.operation_id, pluginId, pluginId, {
                onComplete: enableAfterInstall,
                onFailed: (errorMsg) => showNotification(errorMsg || `Failed to install ${pluginId}`, 'error'),
                onTimeout: () => showNotification(`Install operation timed out for ${pluginId}`, 'error')
            });
        } else {
            // No operation queue configured - install already completed synchronously.
            enableAfterInstall();
        }
    })
    .catch(error => {
        showNotification('Error installing plugin: ' + error.message, 'error');
    });
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
            showNotification(`Plugin ${data.plugin_id} installed successfully`, 'success');
            // Refresh installed plugins and re-render custom registry
            loadInstalledPlugins();
            // Re-render custom registry to update install buttons
            const registryUrlInput = document.getElementById('github-registry-url');
            if (registryUrlInput && registryUrlInput.value.trim()) {
                document.getElementById('load-registry-from-url').click();
            }
        } else {
            showNotification(data.message || 'Installation failed', 'error');
        }
    })
    .catch(error => {
        let errorMsg = 'Error installing plugin: ' + error.message;
        if (isNetworkFailure(error)) {
            errorMsg += ' - Please try refreshing your browser.';
        }
        showNotification(errorMsg, 'error');
    });
}

function setupCollapsibleSections() {
    debugLog('[setupCollapsibleSections] Setting up collapsible sections...');

    // Installed Plugins and Plugin Store sections no longer have collapse buttons
    // They are always visible

    // Functions are now defined outside IIFE, just attach the handler
    if (window.attachGithubTokenCollapseHandler) {
        window.attachGithubTokenCollapseHandler();
    }

    debugLog('[setupCollapsibleSections] Collapsible sections setup complete');
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


    container.innerHTML = repositories.map(repo => {
        const repoUrl = repo.url || '';
        const repoName = repo.name || repoUrl;
        const repoType = repo.type || 'single';

        return `
            <div class="bg-white border border-gray-200 rounded p-2 flex items-center justify-between">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                        <i class="fas ${repoType === 'registry' ? 'fa-folder-open' : 'fa-code-branch'} text-gray-400 text-xs"></i>
                        <span class="text-sm font-medium text-gray-900 truncate" title="${escapeAttribute(repoUrl)}">${escapeHtml(repoName)}</span>
                    </div>
                    <p class="text-xs text-gray-500 truncate" title="${escapeAttribute(repoUrl)}">${escapeHtml(repoUrl)}</p>
                </div>
                <button onclick='if(window.removeSavedRepository){window.removeSavedRepository(${jsStringAttr(repoUrl)})}else{console.error("removeSavedRepository not available")}' class="ml-2 text-red-600 hover:text-red-800 text-xs px-2 py-1" title="Remove repository" aria-label="Remove saved repository ${escapeAttribute(repoName)}">
                    <i class="fas fa-trash" aria-hidden="true"></i>
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
            showNotification('Repository removed successfully', 'success');
            renderSavedRepositories(data.data.repositories || []);
            // Refresh plugin store to remove plugins from deleted repo
            searchPluginStore();
        } else {
            showNotification(data.message || 'Failed to remove repository', 'error');
        }
    })
    .catch(error => {
        showNotification('Error removing repository: ' + error.message, 'error');
    });
}

// Separate function to attach install button handler (can be called multiple times)
function attachInstallButtonHandler() {
    debugLog('[attachInstallButtonHandler] ===== FUNCTION CALLED =====');
    const installBtn = document.getElementById('install-plugin-from-url');
    const pluginUrlInput = document.getElementById('github-plugin-url');
    const pluginStatusDiv = document.getElementById('github-plugin-status');

    debugLog('[attachInstallButtonHandler] Looking for install button elements:', {
        installBtn: !!installBtn,
        pluginUrlInput: !!pluginUrlInput,
        pluginStatusDiv: !!pluginStatusDiv
    });

    if (installBtn && pluginUrlInput) {
        // Check if handler already attached (prevent duplicates)
        if (installBtn.hasAttribute('data-handler-attached')) {
            debugLog('[attachInstallButtonHandler] Handler already attached, skipping');
            return;
        }

        // Clone button to remove any existing listeners (prevents duplicate handlers)
        const parent = installBtn.parentNode;
        if (parent) {
            const newBtn = installBtn.cloneNode(true);
            // Ensure button type is set to prevent form submission
            newBtn.type = 'button';
            // Mark as having handler attached
            newBtn.setAttribute('data-handler-attached', 'true');
            parent.replaceChild(newBtn, installBtn);

            debugLog('[attachInstallButtonHandler] Install button cloned and replaced, type:', newBtn.type);

            newBtn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                debugLog('[attachInstallButtonHandler] Install button clicked!');

                const repoUrl = pluginUrlInput.value.trim();
                if (!repoUrl) {
                    if (pluginStatusDiv) {
                        pluginStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a GitHub URL</span>';
                    }
                    return;
                }

                if (!isGithubUrl(repoUrl)) {
                    if (pluginStatusDiv) {
                        pluginStatusDiv.innerHTML = '<span class="text-red-600"><i class="fas fa-exclamation-circle mr-1"></i>Please enter a valid GitHub URL</span>';
                    }
                    return;
                }

                newBtn.disabled = true;
                newBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Installing...';
                if (pluginStatusDiv) {
                    pluginStatusDiv.innerHTML = '<span class="text-blue-600"><i class="fas fa-spinner fa-spin mr-1"></i>Installing plugin...</span>';
                }

                const branch = document.getElementById('plugin-branch-input')?.value?.trim() || null;
                const requestBody = { repo_url: repoUrl };
                if (branch) {
                    requestBody.branch = branch;
                }

                debugLog('[attachInstallButtonHandler] Sending install request:', requestBody);

                fetch('/api/v3/plugins/install-from-url', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(requestBody)
                })
                .then(response => {
                    debugLog('[attachInstallButtonHandler] Response status:', response.status);
                    return response.json();
                })
                .then(data => {
                    debugLog('[attachInstallButtonHandler] Response data:', data);
                    if (data.status === 'success') {
                        if (pluginStatusDiv) {
                            pluginStatusDiv.innerHTML = `<span class="text-green-600"><i class="fas fa-check-circle mr-1"></i>Successfully installed: ${escapeHtml(data.plugin_id)}</span>`;
                        }
                        pluginUrlInput.value = '';

                        // Refresh installed plugins list
                        setTimeout(() => {
                            loadInstalledPlugins();
                        }, 1000);
                    } else {
                        if (pluginStatusDiv) {
                            pluginStatusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>${escapeHtml(data.message || 'Installation failed')}</span>`;
                        }
                    }
                })
                .catch(error => {
                    console.error('[attachInstallButtonHandler] Error:', error);
                    if (pluginStatusDiv) {
                        pluginStatusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>Error: ${escapeHtml(error.message)}</span>`;
                    }
                })
                .finally(() => {
                    newBtn.disabled = false;
                    newBtn.innerHTML = '<i class="fas fa-download mr-2"></i>Install';
                });
            });

            // Allow Enter key to trigger install
            pluginUrlInput.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    debugLog('[attachInstallButtonHandler] Enter key pressed, triggering install');
                    newBtn.click();
                }
            });

            debugLog('[attachInstallButtonHandler] Install button handler attached successfully');
        } else {
            console.error('[attachInstallButtonHandler] Install button parent not found!');
        }
    } else {
        console.warn('[attachInstallButtonHandler] Install button or URL input not found:', {
            installBtn: !!installBtn,
            pluginUrlInput: !!pluginUrlInput
        });
    }
}

function setupGitHubInstallHandlers() {
    debugLog('[setupGitHubInstallHandlers] ===== FUNCTION CALLED ===== Setting up GitHub install handlers...');

    // Toggle GitHub install section visibility
    const toggleBtn = document.getElementById('toggle-github-install');
    const installSection = document.getElementById('github-install-section');
    const icon = document.getElementById('github-install-icon');

    debugLog('[setupGitHubInstallHandlers] Elements found:', {
        button: !!toggleBtn,
        section: !!installSection,
        icon: !!icon
    });

    if (toggleBtn && installSection) {
        // Clone button to remove any existing listeners
        const parent = toggleBtn.parentNode;
        if (parent) {
            const newBtn = toggleBtn.cloneNode(true);
            parent.replaceChild(newBtn, toggleBtn);

            newBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                debugLog('[setupGitHubInstallHandlers] GitHub install toggle clicked');

                const section = document.getElementById('github-install-section');
                const iconEl = document.getElementById('github-install-icon');
                const btn = document.getElementById('toggle-github-install');

                if (!section || !btn) return;

                const hasHiddenClass = section.classList.contains('hidden');
                const computedDisplay = window.getComputedStyle(section).display;

                if (hasHiddenClass || computedDisplay === 'none') {
                    // Show section - remove hidden, ensure visible
                    section.classList.remove('hidden');
                    section.style.removeProperty('display');
                    if (iconEl) {
                        iconEl.classList.remove('fa-chevron-down');
                        iconEl.classList.add('fa-chevron-up');
                    }
                    const span = btn.querySelector('span');
                    if (span) span.textContent = 'Hide';

                    // Re-attach install button handler when section is shown (in case elements weren't ready before)
                    debugLog('[setupGitHubInstallHandlers] Section shown, will re-attach install button handler in 100ms');
                    setTimeout(() => {
                        debugLog('[setupGitHubInstallHandlers] Re-attaching install button handler now');
                        attachInstallButtonHandler();
                    }, 100);
                } else {
                    // Hide section - add hidden, set display none
                    section.classList.add('hidden');
                    section.style.display = 'none';
                    if (iconEl) {
                        iconEl.classList.remove('fa-chevron-up');
                        iconEl.classList.add('fa-chevron-down');
                    }
                    const span = btn.querySelector('span');
                    if (span) span.textContent = 'Show';
                }
            });
            debugLog('[setupGitHubInstallHandlers] Handler attached');
        }
    } else {
        console.warn('[setupGitHubInstallHandlers] Required elements not found');
    }

    // Install single plugin from URL - use separate function so we can re-call it
    debugLog('[setupGitHubInstallHandlers] About to call attachInstallButtonHandler...');
    attachInstallButtonHandler();
    debugLog('[setupGitHubInstallHandlers] Called attachInstallButtonHandler');

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

            if (!isGithubUrl(repoUrl)) {
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
                registryStatusDiv.innerHTML = `<span class="text-red-600"><i class="fas fa-times-circle mr-1"></i>Error: ${escapeHtml(error.message)}</span>`;
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
                showNotification('Please enter a repository URL first', 'error');
                return;
            }

            if (!isGithubUrl(repoUrl)) {
                showNotification('Please enter a valid GitHub URL', 'error');
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
                    showNotification('Repository saved successfully! Its plugins will appear in the Plugin Store.', 'success');
                    renderSavedRepositories(data.data.repositories || []);
                    // Refresh plugin store to include new repo
                    searchPluginStore();
                } else {
                    showNotification(data.message || 'Failed to save repository', 'error');
                }
            })
            .catch(error => {
                showNotification('Error saving repository: ' + error.message, 'error');
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
            showNotification('Repositories refreshed', 'success');
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


    container.innerHTML = plugins.map(plugin => {
        const isInstalled = isStorePluginInstalled(plugin);
        const pluginIdJs = jsStringAttr(plugin.id);
        const escapedUrlJs = jsStringAttr(registryUrl);
        const pluginPathJs = jsStringAttr(plugin.plugin_path || '');
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

// Replaces the whole Plugin Manager panel. Only for a first load of the
// installed list that failed, when there is nothing else worth keeping on
// screen; every other failure is a notification.
function showInstalledLoadError(message) {
    const content = document.getElementById('plugins-content');
    if (!content) {
        showNotification(message, 'error');
        return;
    }
    content.innerHTML = `
        <div class="text-center py-8">
            <i class="fas fa-exclamation-triangle text-4xl text-red-400 mb-2"></i>
            <p class="text-red-600">${escapeHtml(message)}</p>
        </div>
    `;
}

// fetch() rejects with a TypeError when no HTTP answer arrives at all
// (connection refused, offline); PluginAPI wraps that as NETWORK_ERROR.
function isNetworkFailure(error) {
    return error instanceof TypeError || (error && error.error_code === 'NETWORK_ERROR');
}

// Validate that a URL's actual host is github.com (not just a substring
// match, which 'evil.com/github.com' or 'github.com.evil.com' would pass).
// This is only a UX nicety pointing users at a valid URL - the server does
// its own proper hostname validation before actually acting on the URL.
function isGithubUrl(url) {
    try {
        const hostname = new URL(url).hostname.toLowerCase();
        return hostname === 'github.com' || hostname === 'www.github.com';
    } catch {
        return false;
    }
}

// Short local names for window.LEDEscape (app-early.js), which says what each
// one is for. Function declarations, so they are usable anywhere in this IIFE.
function escapeHtml(text) { return window.LEDEscape.html(text); }
function escapeAttribute(text) { return window.LEDEscape.attr(text); }
function jsStringAttr(value) { return window.LEDEscape.jsStringAttr(value); }

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
window.openGithubTokenSettings = function() {
    const settings = document.getElementById('github-token-settings');
    const warning = document.getElementById('github-auth-warning');
    const tokenContent = document.getElementById('github-token-content');

    if (settings) {
        // Show settings panel using both methods
        settings.classList.remove('hidden');
        settings.style.display = '';

                        // Expand the content when opening
                        if (tokenContent) {
                            tokenContent.style.removeProperty('display');
                            tokenContent.classList.remove('hidden');

            // Update collapse button state
            const tokenIconCollapse = document.getElementById('github-token-icon-collapse');
            const toggleTokenCollapseBtn = document.getElementById('toggle-github-token-collapse');
            if (tokenIconCollapse) {
                tokenIconCollapse.classList.remove('fa-chevron-down');
                tokenIconCollapse.classList.add('fa-chevron-up');
            }
            if (toggleTokenCollapseBtn) {
                const span = toggleTokenCollapseBtn.querySelector('span');
                if (span) span.textContent = 'Collapse';
            }
        }

        // When opening settings, hide the warning banner
        if (warning) {
            warning.classList.add('hidden');
            warning.style.display = 'none';
            // Clear any dismissal state since user is actively configuring
            sessionStorage.removeItem('github-auth-warning-dismissed');
        }

        // Load token when opening the panel
        loadGithubToken();
    }
}

window.toggleGithubTokenVisibility = function() {
    const input = document.getElementById('github-token-input');
    const icon = document.getElementById('github-token-icon');

    if (input && icon) {
        const btn = icon.closest('button');
        if (input.type === 'password') {
            input.type = 'text';
            icon.classList.remove('fa-eye');
            icon.classList.add('fa-eye-slash');
            if (btn) btn.setAttribute('aria-label', 'Hide token');
        } else {
            input.type = 'password';
            icon.classList.remove('fa-eye-slash');
            icon.classList.add('fa-eye');
            if (btn) btn.setAttribute('aria-label', 'Show token');
        }
    }
}

window.loadGithubToken = function() {
    const input = document.getElementById('github-token-input');
    const loadButton = document.querySelector('button[onclick="loadGithubToken()"]');

    if (!input) return;

    // Set loading state on load button
    const originalButtonContent = loadButton ? loadButton.innerHTML : '';
    if (loadButton) {
        loadButton.disabled = true;
        loadButton.classList.add('opacity-50', 'cursor-not-allowed');
        loadButton.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Loading...';
    }

    fetch('/api/v3/config/secrets')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                // Handle empty data (secrets file doesn't exist) - API returns {} in this case
                const secrets = data.data || {};
                const token = secrets.github?.api_token || '';
                const configured = token && token !== 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN';

                if (input) {
                    // The endpoint masks what it returns, so this never holds
                    // the real token -- and the field is deliberately left
                    // empty rather than filled with the mask, which would be
                    // saved verbatim the next time the user pressed Save.
                    input.value = '';
                    if (configured) {
                        showNotification('A GitHub token is saved. Enter a new one to replace it.', 'success');
                    } else {
                        showNotification('No GitHub token configured. Enter a new token to save.', 'info');
                    }
                }
            } else {
                throw new Error(data.message || 'Failed to load secrets configuration');
            }
        })
        .catch(error => {
            console.error('Error loading GitHub token:', error);
            if (input) {
                input.value = '';
            }
            // If it's a 404 or file doesn't exist, that's okay - just inform the user
            if (error.message.includes('404') || error.message.includes('not found')) {
                showNotification('No secrets file found. You can create one by saving a token.', 'info');
            } else {
                showNotification('Error loading GitHub token: ' + error.message, 'error');
            }
        })
        .finally(() => {
            // Restore button state
            if (loadButton) {
                loadButton.disabled = false;
                loadButton.classList.remove('opacity-50', 'cursor-not-allowed');
                loadButton.innerHTML = originalButtonContent;
            }
        });
}

window.saveGithubToken = function() {
    const input = document.getElementById('github-token-input');
    const saveButton = document.querySelector('button[onclick="saveGithubToken()"]');
    if (!input) return;

    const token = input.value.trim();

    if (!token) {
        showNotification('Please enter a GitHub token', 'error');
        return;
    }

    // Client-side token validation
    if (!token.startsWith('ghp_') && !token.startsWith('github_pat_')) {
        if (!confirm('Token format looks invalid. GitHub tokens should start with "ghp_" or "github_pat_". Continue anyway?')) {
            return;
        }
    }

    // Set loading state on save button
    const originalButtonContent = saveButton ? saveButton.innerHTML : '';
    if (saveButton) {
        saveButton.disabled = true;
        saveButton.classList.add('opacity-50', 'cursor-not-allowed');
        saveButton.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Saving...';
    }

    // Load current secrets config
    fetch('/api/v3/config/secrets')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
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
                throw new Error(data.message || 'Failed to load current secrets');
            }
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                showNotification('GitHub token saved successfully! Rate limit increased to 5,000/hour', 'success');

                // Clear input field for security (user can reload if needed)
                input.value = '';

                // Clear the dismissal flag so warning can properly hide/show based on token status
                sessionStorage.removeItem('github-auth-warning-dismissed');

                // Small delay to ensure backend has reloaded the token, then refresh status
                // checkGitHubAuthStatus() will handle collapsing the panel automatically
                // Reduced delay from 300ms to 100ms - backend should reload quickly
                setTimeout(() => {
                    if (window.checkGitHubAuthStatus) {
                        window.checkGitHubAuthStatus();
                    }
                }, 100);
            } else {
                throw new Error(data.message || 'Failed to save token');
            }
        })
        .catch(error => {
            console.error('Error saving GitHub token:', error);
            showNotification('Error saving GitHub token: ' + error.message, 'error');
        })
        .finally(() => {
            // Restore button state
            if (saveButton) {
                saveButton.disabled = false;
                saveButton.classList.remove('opacity-50', 'cursor-not-allowed');
                saveButton.innerHTML = originalButtonContent;
            }
        });
}


window.dismissGithubWarning = function() {
    const warning = document.getElementById('github-auth-warning');
    const settings = document.getElementById('github-token-settings');
    if (warning) {
        // Hide warning using both classList and style.display
        warning.classList.add('hidden');
        warning.style.display = 'none';
        // Also hide settings if it's open (since they're combined now)
        if (settings && !settings.classList.contains('hidden')) {
            settings.classList.add('hidden');
            settings.style.display = 'none';
        }
        // Remember dismissal for this session
        sessionStorage.setItem('github-auth-warning-dismissed', 'true');
    }
}


// Expose renderArrayObjectItem, getSchemaProperty, and escapeHtml to window for use by global functions
window.renderArrayObjectItem = renderArrayObjectItem;
window.getSchemaProperty = getSchemaProperty;

// Expose GitHub install handlers. These must be assigned inside the IIFE —
// from outside the IIFE, `typeof attachInstallButtonHandler` evaluates to
// 'undefined' and the fallback path at the bottom of this file fires a
// [FALLBACK] attachInstallButtonHandler not available on window warning.
window.attachInstallButtonHandler = attachInstallButtonHandler;
window.setupGitHubInstallHandlers = setupGitHubInstallHandlers;

})(); // End IIFE

// Functions to handle array-of-objects
// Define these at the top level (outside any IIFE) to ensure they're always available
if (typeof window !== 'undefined') {
    window.addArrayObjectItem = function(fieldId, fullKey, maxItems) {
        const itemsContainer = document.getElementById(fieldId + '_items');
        const hiddenInput = document.getElementById(fieldId + '_data');
        if (!itemsContainer || !hiddenInput) return;

        const currentItems = itemsContainer.querySelectorAll('.array-object-item');
        if (currentItems.length >= maxItems) {
            alert(`Maximum ${maxItems} items allowed`);
            return;
        }

        // Get schema for item properties - ensure currentPluginConfig is available
        // Try window.currentPluginConfig first (most reliable), then currentPluginConfig
        const schema = (typeof window.currentPluginConfig !== 'undefined' && window.currentPluginConfig?.schema) ||
                       (typeof currentPluginConfig !== 'undefined' && currentPluginConfig?.schema);
        if (!schema) {
            console.error('addArrayObjectItem: Schema not available. currentPluginConfig may not be set.');
            return;
        }

        // Use getSchemaProperty to properly handle nested schemas (e.g., news.custom_feeds)
        const arraySchema = window.getSchemaProperty(schema, fullKey);
        if (!arraySchema || arraySchema.type !== 'array' || !arraySchema.items) {
            return;
        }

        const itemsSchema = arraySchema.items;
        if (!itemsSchema || !itemsSchema.properties) return;

        const newIndex = currentItems.length;
        // renderArrayObjectItem is exported by the plugin-manager IIFE above.
        const itemHtml = window.renderArrayObjectItem(fieldId, fullKey, itemsSchema.properties, {}, newIndex, itemsSchema);
        itemsContainer.insertAdjacentHTML('beforeend', itemHtml);
        window.updateArrayObjectData(fieldId);

        // Update add button state
        const addButton = itemsContainer.nextElementSibling;
        if (addButton && currentItems.length + 1 >= maxItems) {
            addButton.disabled = true;
            addButton.style.opacity = '0.5';
            addButton.style.cursor = 'not-allowed';
        }
    };

    window.removeArrayObjectItem = function(fieldId, index) {
        const itemsContainer = document.getElementById(fieldId + '_items');
        if (!itemsContainer) return;

        const item = itemsContainer.querySelector(`.array-object-item[data-index="${index}"]`);
        if (item) {
            item.remove();
            // Re-index remaining items
            // Use data-index for index storage - no need to encode index in onclick strings or IDs
            const remainingItems = itemsContainer.querySelectorAll('.array-object-item');
            remainingItems.forEach((itemEl, newIndex) => {
                itemEl.setAttribute('data-index', newIndex);
                // Update all inputs within this item - only update index in array bracket notation
                itemEl.querySelectorAll('input, select, textarea').forEach(input => {
                    const name = input.getAttribute('name');
                    const id = input.id;
                    if (name) {
                        // Only replace index in bracket notation like [0], [1], etc.
                        // Match pattern: field_name[index] but not field_name123
                        const newName = name.replace(/\[(\d+)\]/, `[${newIndex}]`);
                        input.setAttribute('name', newName);
                    }
                    if (id) {
                        // Only update index in specific patterns like _item_0, _item_1
                        // Match pattern: _item_<digits> but be careful not to break other numeric IDs
                        const newId = id.replace(/_item_(\d+)/, `_item_${newIndex}`);
                        input.id = newId;
                    }
                });
                // Update button onclick attributes - only update the index parameter
                // Since we use data-index for tracking, we can compute index from closest('.array-object-item')
                // For now, update onclick strings but be more careful with the regex
                itemEl.querySelectorAll('button[onclick]').forEach(button => {
                    const onclick = button.getAttribute('onclick');
                    if (onclick) {
                        // Match patterns like:
                        // removeArrayObjectItem('fieldId', 0)
                        // handleArrayObjectFileUpload(event, 'fieldId', 0, 'propKey', 'pluginId')
                        // removeArrayObjectFile('fieldId', 0, 'propKey')
                        // Only replace the numeric index parameter (second or third argument depending on function)
                        let newOnclick = onclick;
                        // For removeArrayObjectItem('fieldId', index) - second param
                        newOnclick = newOnclick.replace(
                            /removeArrayObjectItem\s*\(\s*['"]([^'"]+)['"]\s*,\s*\d+\s*\)/g,
                            `removeArrayObjectItem('$1', ${newIndex})`
                        );
                        // For handleArrayObjectFileUpload(event, 'fieldId', index, ...) - third param
                        newOnclick = newOnclick.replace(
                            /handleArrayObjectFileUpload\s*\(\s*event\s*,\s*['"]([^'"]+)['"]\s*,\s*\d+\s*,/g,
                            `handleArrayObjectFileUpload(event, '$1', ${newIndex},`
                        );
                        // For removeArrayObjectFile('fieldId', index, ...) - second param
                        newOnclick = newOnclick.replace(
                            /removeArrayObjectFile\s*\(\s*['"]([^'"]+)['"]\s*,\s*\d+\s*,/g,
                            `removeArrayObjectFile('$1', ${newIndex},`
                        );
                        button.setAttribute('onclick', newOnclick);
                    }
                });
            });
            window.updateArrayObjectData(fieldId);

            // Update add button state
            const addButton = itemsContainer.nextElementSibling;
            if (addButton && addButton.getAttribute('onclick')) {
                // Extract maxItems from onclick attribute more safely
                // Pattern: addArrayObjectItem('fieldId', 'fullKey', maxItems)
                const onclickMatch = addButton.getAttribute('onclick').match(/addArrayObjectItem\s*\([^,]+,\s*[^,]+,\s*(\d+)\)/);
                if (onclickMatch && onclickMatch[1]) {
                    const maxItems = parseInt(onclickMatch[1]);
                    if (remainingItems.length < maxItems) {
                        addButton.disabled = false;
                        addButton.style.opacity = '1';
                        addButton.style.cursor = 'pointer';
                    }
                }
            }
        }
    };

    // Debug logging (only if pluginDebug is enabled)
    if (_PLUGIN_DEBUG_EARLY) {
        debugLog('[ARRAY-OBJECTS] Functions defined on window:', {
            addArrayObjectItem: typeof window.addArrayObjectItem,
            removeArrayObjectItem: typeof window.removeArrayObjectItem,
            updateArrayObjectData: typeof window.updateArrayObjectData,
            handleArrayObjectFileUpload: typeof window.handleArrayObjectFileUpload,
            removeArrayObjectFile: typeof window.removeArrayObjectFile
        });
    }
}

// Make currentPluginConfig globally accessible (outside IIFE)
window.currentPluginConfig = null;

// Force initialization immediately when script loads (for HTMX swapped content)
debugLog('Plugins script loaded, checking for elements...');

// Verify critical functions are available
if (_PLUGIN_DEBUG_EARLY) {
    debugLog('Plugin functions available:', {
        configurePlugin: typeof window.configurePlugin,
        togglePlugin: typeof window.togglePlugin,
        initializePlugins: typeof window.initializePlugins,
        loadInstalledPlugins: typeof window.loadInstalledPlugins,
        searchPluginStore: typeof window.searchPluginStore
    });
}

// Check GitHub auth status immediately if elements exist (don't wait for full initialization)
if (window.checkGitHubAuthStatus && document.getElementById('github-auth-warning')) {
    debugLog('[EARLY] Checking GitHub auth status immediately on script load...');
    window.checkGitHubAuthStatus();
}

setTimeout(function() {
    const installedGrid = document.getElementById('installed-plugins-grid');
    if (installedGrid) {
        debugLog('Found installed-plugins-grid, forcing initialization...');
        window.pluginManager.initialized = false;
        if (typeof initializePluginPageWhenReady === 'function') {
            initializePluginPageWhenReady();
        } else if (typeof window.initPluginsPage === 'function') {
            window.initPluginsPage();
        }
    } else {
        debugLog('installed-plugins-grid not found yet, will retry via event listeners');
    }

    // Also try to attach install button handler after a delay (fallback).
    // Only run if the install button element is already in the DOM (i.e. the
    // plugins partial has been loaded); otherwise the htmx:afterSettle listener
    // below handles it when the tab is first visited.
    setTimeout(() => {
        if (typeof window.attachInstallButtonHandler === 'function' &&
            document.getElementById('install-plugin-from-url')) {
            window.attachInstallButtonHandler();
        }
    }, 500);
}, 200);

// Re-run install button wiring after HTMX settles the plugins tab content.
// Guard with element check so it only fires when the plugins partial is in the DOM,
// preventing spurious warnings on other tab loads.
document.addEventListener('htmx:afterSettle', function() {
    if (document.getElementById('install-plugin-from-url') &&
        typeof window.attachInstallButtonHandler === 'function') {
        window.attachInstallButtonHandler();
    }
});

// ─── Starlark Apps Integration ──────────────────────────────────────────────

(function() {
    'use strict';

    let starlarkSectionVisible = false;
    let starlarkFullCache = null;       // All apps from server
    let starlarkDataLoaded = false;

    // ── Helpers ─────────────────────────────────────────────────────────────
    function escapeHtml(str) { return window.LEDEscape.html(str); }

    function isStarlarkInstalled(appId) {
        // Check window.installedPlugins (populated by loadInstalledPlugins)
        if (window.installedPlugins && Array.isArray(window.installedPlugins)) {
            return window.installedPlugins.some(p => p.id === 'starlark:' + appId);
        }
        return false;
    }

    // ── Section Toggle + Init ───────────────────────────────────────────────
    function initStarlarkSection() {
        const toggleBtn = document.getElementById('toggle-starlark-section');
        if (toggleBtn && !toggleBtn._starlarkInit) {
            toggleBtn._starlarkInit = true;
            toggleBtn.addEventListener('click', function() {
                starlarkSectionVisible = !starlarkSectionVisible;
                const content = document.getElementById('starlark-section-content');
                const icon = document.getElementById('starlark-section-icon');
                if (content) content.classList.toggle('hidden', !starlarkSectionVisible);
                if (icon) {
                    icon.classList.toggle('fa-chevron-down', !starlarkSectionVisible);
                    icon.classList.toggle('fa-chevron-up', starlarkSectionVisible);
                }
                this.querySelector('span').textContent = starlarkSectionVisible ? 'Hide' : 'Show';
                if (starlarkSectionVisible) {
                    loadStarlarkStatus();
                    if (!starlarkDataLoaded) fetchStarlarkApps();
                }
            });
        }

        // Persisted sort/perPage are restored by the controller's syncControls().
        setupStarlarkFilterListeners();

        const uploadBtn = document.getElementById('starlark-upload-btn');
        if (uploadBtn && !uploadBtn._starlarkInit) {
            uploadBtn._starlarkInit = true;
            uploadBtn.addEventListener('click', function() {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = '.star';
                input.onchange = function(e) {
                    if (e.target.files.length > 0) uploadStarlarkFile(e.target.files[0]);
                };
                input.click();
            });
        }
    }

    // ── Status ──────────────────────────────────────────────────────────────
    function loadStarlarkStatus() {
        fetch('/api/v3/starlark/status')
            .then(r => r.json())
            .then(data => {
                const banner = document.getElementById('starlark-pixlet-status');
                if (!banner) return;
                if (data.pixlet_available) {
                    banner.innerHTML = `<div class="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800">
                        <i class="fas fa-check-circle mr-2"></i>Pixlet available${data.pixlet_version ? ' (' + escapeHtml(data.pixlet_version) + ')' : ''} &mdash; ${data.installed_apps || 0} app(s) installed
                    </div>`;
                } else {
                    banner.innerHTML = `<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm text-yellow-800">
                        <i class="fas fa-exclamation-triangle mr-2"></i>Pixlet not installed.
                        <button onclick="window.installPixlet()" class="ml-2 px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs font-semibold">Install Pixlet</button>
                    </div>`;
                }
            })
            .catch(err => console.error('Starlark status error:', err));
    }

    // ── Bulk Fetch All Apps ─────────────────────────────────────────────────
    function fetchStarlarkApps() {
        const grid = document.getElementById('starlark-apps-grid');
        if (grid) {
            grid.innerHTML = `<div class="col-span-full">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
                    ${Array(10).fill('<div class="bg-gray-200 rounded-lg p-4 h-48 animate-pulse"></div>').join('')}
                </div>
            </div>`;
        }

        fetch('/api/v3/starlark/repository/browse')
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'success') {
                    if (grid) grid.innerHTML = `<div class="col-span-full text-center py-8 text-red-500"><i class="fas fa-exclamation-circle mr-2"></i>${escapeHtml(data.message || 'Failed to load')}</div>`;
                    return;
                }

                starlarkFullCache = data.apps || [];
                starlarkDataLoaded = true;

                // Populate category dropdown
                const catSelect = document.getElementById('starlark-category');
                if (catSelect) {
                    catSelect.innerHTML = '<option value="">All Categories</option>';
                    (data.categories || []).forEach(cat => {
                        const opt = document.createElement('option');
                        opt.value = cat;
                        opt.textContent = cat;
                        catSelect.appendChild(opt);
                    });
                }

                // Populate author dropdown
                const authSelect = document.getElementById('starlark-filter-author');
                if (authSelect) {
                    authSelect.innerHTML = '<option value="">All Authors</option>';
                    (data.authors || []).forEach(author => {
                        const opt = document.createElement('option');
                        opt.value = author;
                        opt.textContent = author;
                        authSelect.appendChild(opt);
                    });
                }

                const countEl = document.getElementById('starlark-apps-count');
                if (countEl) countEl.textContent = `${data.count} apps`;

                if (data.rate_limit) {
                    debugLog(`[Starlark] GitHub rate limit: ${data.rate_limit.remaining}/${data.rate_limit.limit} remaining` + (data.cached ? ' (cached)' : ''));
                }

                applyStarlarkFiltersAndSort();
            })
            .catch(err => {
                console.error('Starlark browse error:', err);
                if (grid) grid.innerHTML = '<div class="col-span-full text-center py-8 text-red-500"><i class="fas fa-exclamation-circle mr-2"></i>Error loading apps</div>';
            });
    }

    // ── Apply Filters + Sort ────────────────────────────────────────────────
    // ── Filter / Sort / Paginate ────────────────────────────────────────────
    // Same behaviour, element ids and localStorage keys as the hand-rolled
    // version this replaces; the machinery is now shared with the store and
    // the installed-plugins list.
    function starlarkSortName(app) {
        return (app.name || app.id || '').toLowerCase();
    }

    const STARLARK_COMPARATORS = {
        'a-z': (a, b) => starlarkSortName(a).localeCompare(starlarkSortName(b)),
        'z-a': (a, b) => starlarkSortName(b).localeCompare(starlarkSortName(a)),
        'category': (a, b) => {
            const catCmp = (a.category || '').localeCompare(b.category || '');
            return catCmp !== 0 ? catCmp : starlarkSortName(a).localeCompare(starlarkSortName(b));
        },
        'author': (a, b) => {
            const authCmp = (a.author || '').localeCompare(b.author || '');
            return authCmp !== 0 ? authCmp : starlarkSortName(a).localeCompare(starlarkSortName(b));
        },
    };

    function renderInstalledCycleButton(btn, value) {
        if (value === true) {
            btn.innerHTML = '<i class="fas fa-check-circle mr-1 text-green-500"></i>Installed';
            btn.classList.add('border-green-400', 'bg-green-50');
            btn.classList.remove('border-gray-300', 'bg-white', 'border-red-400', 'bg-red-50');
        } else if (value === false) {
            btn.innerHTML = '<i class="fas fa-times-circle mr-1 text-red-500"></i>Not Installed';
            btn.classList.add('border-red-400', 'bg-red-50');
            btn.classList.remove('border-gray-300', 'bg-white', 'border-green-400', 'bg-green-50');
        } else {
            btn.innerHTML = '<i class="fas fa-filter mr-1 text-gray-400"></i>All';
            btn.classList.add('border-gray-300', 'bg-white');
            btn.classList.remove('border-green-400', 'bg-green-50', 'border-red-400', 'bg-red-50');
        }
    }

    let _starlarkFilter = null;

    function getStarlarkFilter() {
        if (_starlarkFilter) return _starlarkFilter;
        if (!window.ListFilter || typeof window.ListFilter.create !== 'function') {
            console.warn('[STARLARK] ListFilter helper unavailable — filters disabled');
            return null;
        }
        _starlarkFilter = window.ListFilter.create({
            getItems: () => starlarkFullCache || [],
            render: (pageApps) => renderStarlarkApps(pageApps, document.getElementById('starlark-apps-grid')),
            search: {
                el: 'starlark-search',
                fields: ['name', 'summary', 'desc', 'author', 'id', 'category'],
                debounceMs: 150,
            },
            sort: {
                el: 'starlark-sort',
                default: 'a-z',
                comparators: STARLARK_COMPARATORS,
            },
            controls: [
                {
                    type: 'select',
                    el: 'starlark-category',
                    key: 'filterCategory',
                    default: '',
                    test: (app, value) => (app.category || '').toLowerCase() === value.toLowerCase(),
                },
                {
                    // Author matching is exact/case-sensitive — the options come
                    // straight from the server's author list.
                    type: 'select',
                    el: 'starlark-filter-author',
                    key: 'filterAuthor',
                    default: '',
                    test: (app, value) => app.author === value,
                },
                {
                    type: 'cycle',
                    el: 'starlark-filter-installed',
                    key: 'filterInstalled',
                    default: null,
                    values: [null, true, false],
                    test: (app, value) => (value === true ? isStarlarkInstalled(app.id) : !isStarlarkInstalled(app.id)),
                    render: renderInstalledCycleButton,
                },
            ],
            pagination: {
                perPageEl: 'starlark-per-page',
                defaultPerPage: 24,
                topEl: 'starlark-pagination-top',
                bottomEl: 'starlark-pagination-bottom',
                infoEl: 'starlark-results-info',
                infoBottomEl: 'starlark-results-info-bottom',
                scrollToEl: 'starlark-apps-grid',
                infoFormat: (start, end, total) => `Showing ${start}\u2013${end} of ${total} apps`,
                emptyText: 'No apps match your filters',
            },
            persist: {
                read: () => ({
                    sort: safeLocalStorage.getItem('starlarkSort') || undefined,
                    perPage: parseInt(safeLocalStorage.getItem('starlarkPerPage')) || undefined,
                }),
                write: (state) => {
                    safeLocalStorage.setItem('starlarkSort', state.sort);
                    safeLocalStorage.setItem('starlarkPerPage', state.perPage);
                },
            },
            clearEl: 'starlark-clear-filters',
            activeCountEl: 'starlark-active-filters',
        });
        return _starlarkFilter;
    }

    function applyStarlarkFiltersAndSort(skipPageReset) {
        if (!starlarkFullCache) return;
        const ctl = getStarlarkFilter();
        if (ctl) {
            ctl.apply(skipPageReset);
            return;
        }
        renderStarlarkApps(starlarkFullCache, document.getElementById('starlark-apps-grid'));
    }

    // ── Card Rendering ──────────────────────────────────────────────────────
    function renderStarlarkApps(apps, grid) {
        if (!grid) return;
        if (!apps || apps.length === 0) {
            grid.innerHTML = '<div class="col-span-full empty-state"><div class="empty-state-icon"><i class="fas fa-star"></i></div><p>No Starlark apps found</p></div>';
            return;
        }

        const setGridHtmlIfChanged = window.__pmSetGridHtmlIfChanged ||
            ((el, html) => { el.innerHTML = html; return true; });
        setGridHtmlIfChanged(grid, apps.map(app => {
            const installed = isStarlarkInstalled(app.id);
            return `
            <div class="plugin-card" data-app-id="${escapeHtml(app.id)}">
                <div class="flex items-start justify-between mb-4">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center flex-wrap gap-1.5 mb-2">
                            <!-- break-words: Starlark app names come from the
                                 community repo and some are long single tokens,
                                 which overflowed the card instead of wrapping. -->
                            <h4 class="font-semibold text-gray-900 text-base break-words">${escapeHtml(app.name || app.id)}</h4>
                            <span class="badge badge-warning"><i class="fas fa-star mr-1"></i>Starlark</span>
                            ${installed ? '<span class="badge badge-success"><i class="fas fa-check mr-1"></i>Installed</span>' : ''}
                        </div>
                        <div class="text-sm text-gray-600 space-y-1.5 mb-3">
                            ${app.author ? `<p class="flex items-center"><i class="fas fa-user mr-2 text-gray-400 w-4"></i>${escapeHtml(app.author)}</p>` : ''}
                            ${app.category ? `<p class="flex items-center"><i class="fas fa-folder mr-2 text-gray-400 w-4"></i>${escapeHtml(app.category)}</p>` : ''}
                        </div>
                        <p class="text-sm text-gray-700 leading-relaxed">${escapeHtml(app.summary || app.desc || 'No description')}</p>
                    </div>
                </div>
                <div class="flex gap-2 mt-auto pt-3 border-t border-gray-200">
                    <button data-action="install" class="btn ${installed ? 'bg-gray-500 hover:bg-gray-600' : 'bg-green-600 hover:bg-green-700'} text-white px-4 py-2 rounded-md text-sm font-semibold flex-1 flex justify-center items-center">
                        <i class="fas ${installed ? 'fa-redo' : 'fa-download'} mr-2"></i>${installed ? 'Reinstall' : 'Install'}
                    </button>
                    <button data-action="view" class="btn bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md text-sm font-semibold flex justify-center items-center">
                        <i class="fas fa-external-link-alt mr-1"></i>View
                    </button>
                </div>
            </div>`;
        }).join(''));

        // Add delegated event listener only once (prevent duplicate handlers)
        if (!grid.dataset.starlarkHandlerAttached) {
            grid.addEventListener('click', function handleStarlarkGridClick(e) {
                const button = e.target.closest('button[data-action]');
                if (!button) return;

                const card = button.closest('.plugin-card');
                if (!card) return;

                const appId = card.dataset.appId;
                if (!appId) return;

                const action = button.dataset.action;
                if (action === 'install') {
                    window.installStarlarkApp(appId);
                } else if (action === 'view') {
                    window.open('https://github.com/tronbyt/apps/tree/main/apps/' + encodeURIComponent(appId), '_blank');
                }
            });
            grid.dataset.starlarkHandlerAttached = 'true';
        }
    }

    // ── Filter UI Updates ───────────────────────────────────────────────────
    // ── Event Listeners ─────────────────────────────────────────────────────
    function setupStarlarkFilterListeners() {
        const ctl = getStarlarkFilter();
        if (!ctl) return;
        ctl.bind();
        ctl.syncControls();
    }

    // ── Install / Upload / Pixlet ───────────────────────────────────────────
    window.installStarlarkApp = function(appId) {
        if (!confirm(`Install Starlark app "${appId}" from Tronbyte repository?`)) return;

        fetch('/api/v3/starlark/repository/install', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({app_id: appId})
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                alert(`Installed: ${data.message || appId}`);
                // Refresh installed plugins list
                if (typeof loadInstalledPlugins === 'function') loadInstalledPlugins();
                else if (typeof window.loadInstalledPlugins === 'function') window.loadInstalledPlugins();
                // Re-render current page to update installed badges
                setTimeout(() => applyStarlarkFiltersAndSort(true), 500);
            } else {
                alert(`Install failed: ${data.message || 'Unknown error'}`);
            }
        })
        .catch(err => {
            console.error('Install error:', err);
            alert('Install failed: ' + err.message);
        });
    };

    window.installPixlet = function() {
        if (!confirm('Download and install Pixlet binary? This may take a few minutes.')) return;

        fetch('/api/v3/starlark/install-pixlet', {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    alert(data.message || 'Pixlet installed!');
                    loadStarlarkStatus();
                } else {
                    alert('Pixlet install failed: ' + (data.message || 'Unknown error'));
                }
            })
            .catch(err => alert('Pixlet install failed: ' + err.message));
    };

    function uploadStarlarkFile(file) {
        const formData = new FormData();
        formData.append('file', file);

        const appId = file.name.replace('.star', '');
        formData.append('app_id', appId);
        formData.append('name', appId.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));

        fetch('/api/v3/starlark/upload', {method: 'POST', body: formData})
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    alert(`Uploaded: ${data.app_id}`);
                    if (typeof loadInstalledPlugins === 'function') loadInstalledPlugins();
                    else if (typeof window.loadInstalledPlugins === 'function') window.loadInstalledPlugins();
                    setTimeout(() => applyStarlarkFiltersAndSort(true), 500);
                } else {
                    alert('Upload failed: ' + (data.message || 'Unknown error'));
                }
            })
            .catch(err => alert('Upload failed: ' + err.message));
    }

    // ── Bootstrap ───────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', initStarlarkSection);
    document.addEventListener('htmx:afterSwap', function(e) {
        if (e.detail && e.detail.target && e.detail.target.id === 'plugins-content') {
            initStarlarkSection();
        }
    });
})();
