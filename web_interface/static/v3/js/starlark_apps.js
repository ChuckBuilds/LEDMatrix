/**
 * Starlark Apps Manager - Frontend JavaScript
 *
 * Handles UI interactions for managing Starlark (.star) apps
 */

(function() {
    'use strict';

    let currentConfigAppId = null;
    let repositoryApps = [];
    let repositoryCategories = [];
    
    // Track grids that already have event listeners to prevent duplicates
    const gridsWithListeners = new WeakSet();
    const repoGridsWithListeners = new WeakSet();

    // ========================================================================
    // Security: HTML Sanitization
    // ========================================================================

    /**
     * Sanitize HTML string to prevent XSS attacks.
     * Escapes HTML special characters.
     */
    function sanitizeHtml(str) {
        if (str === null || str === undefined) {
            return '';
        }
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    // Define init function first
    function initStarlarkApps() {
        console.log('[Starlark] initStarlarkApps called, initialized:', window.starlarkAppsInitialized);

        try {
            // Set up event listeners only once to prevent duplicates
            if (!window.starlarkAppsInitialized) {
                window.starlarkAppsInitialized = true;
                setupEventListeners();
                setupRepositoryListeners();
                console.log('[Starlark] Event listeners set up');
            }

            // Always load data when init is called (handles tab switching)
            console.log('[Starlark] Loading status and apps...');
            loadStarlarkStatus();
            loadStarlarkApps();
        } catch (error) {
            console.error('[Starlark] Error in initStarlarkApps:', error);
        }
    }

    // Expose init function globally BEFORE auto-init
    window.initStarlarkApps = initStarlarkApps;

    // Initialize on page load - but DON'T auto-init when loaded dynamically
    // Let the HTML partial's script handle initialization for HTMX swaps
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            console.log('[Starlark] DOMContentLoaded, calling init');
            initStarlarkApps();
        });
    }
    // Note: Removed the else block - dynamic loading handled by HTML partial

    function setupEventListeners() {
        // Upload button
        const uploadBtn = document.getElementById('upload-star-btn');
        if (uploadBtn) {
            uploadBtn.addEventListener('click', openUploadModal);
        }

        // Refresh button
        const refreshBtn = document.getElementById('refresh-starlark-apps-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', function() {
                loadStarlarkApps();
                showNotification('Refreshing apps...', 'info');
            });
        }

        // Upload form
        const uploadForm = document.getElementById('upload-star-form');
        if (uploadForm) {
            uploadForm.addEventListener('submit', handleUploadSubmit);
        }

        // File input and drop zone
        const fileInput = document.getElementById('star-file-input');
        const dropZone = document.getElementById('upload-drop-zone');

        if (fileInput && dropZone) {
            dropZone.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', handleFileSelect);

            // Drag and drop
            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.classList.add('border-blue-500', 'bg-blue-50');
            });

            dropZone.addEventListener('dragleave', () => {
                dropZone.classList.remove('border-blue-500', 'bg-blue-50');
            });

            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.classList.remove('border-blue-500', 'bg-blue-50');

                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    // Create DataTransfer to properly assign files across browsers
                    const dataTransfer = new DataTransfer();
                    for (let i = 0; i < files.length; i++) {
                        dataTransfer.items.add(files[i]);
                    }
                    fileInput.files = dataTransfer.files;
                    handleFileSelect({ target: fileInput });
                }
            });
        }

        // Config form
        const configForm = document.getElementById('starlark-config-form');
        if (configForm) {
            configForm.addEventListener('submit', handleConfigSubmit);
        }
    }

    function handleFileSelect(event) {
        const file = event.target.files[0];
        const fileNameDisplay = document.getElementById('selected-file-name');

        if (file) {
            fileNameDisplay.textContent = `Selected: ${file.name}`;
            fileNameDisplay.classList.remove('hidden');

            // Auto-fill app name from filename
            const appNameInput = document.getElementById('star-app-name');
            if (appNameInput && !appNameInput.value) {
                const baseName = file.name.replace('.star', '').replace(/[_-]/g, ' ');
                appNameInput.value = baseName.charAt(0).toUpperCase() + baseName.slice(1);
            }
        } else {
            fileNameDisplay.classList.add('hidden');
        }
    }

    async function loadStarlarkStatus() {
        console.log('[Starlark] loadStarlarkStatus called');
        try {
            const response = await fetch('/api/v3/starlark/status');
            const data = await response.json();
            console.log('[Starlark] Status API response:', data);

            const banner = document.getElementById('pixlet-status-banner');
            console.log('[Starlark] Banner element found:', !!banner);
            if (!banner) return;

            // Check if the plugin itself is not installed (different from Pixlet not being available)
            if (data.status === 'error' && data.message && data.message.includes('plugin not installed')) {
                banner.className = 'mb-6 p-4 rounded-lg border border-red-400 bg-red-50';
                banner.innerHTML = `
                    <div class="flex items-start">
                        <i class="fas fa-exclamation-circle text-red-600 text-xl mr-3 mt-1"></i>
                        <div class="flex-1">
                            <h4 class="font-semibold text-red-900 mb-1">Starlark Apps Plugin Not Active</h4>
                            <p class="text-sm text-red-800 mb-2">The Starlark Apps plugin needs to be discovered and enabled. This usually happens after a server restart.</p>
                            <p class="text-xs text-red-700">Try refreshing the page or restarting the LEDMatrix service.</p>
                        </div>
                    </div>
                `;
            } else if (data.status === 'error' || !data.pixlet_available) {
                banner.className = 'mb-6 p-4 rounded-lg border border-yellow-400 bg-yellow-50';
                banner.innerHTML = `
                    <div class="flex items-start">
                        <i class="fas fa-exclamation-triangle text-yellow-600 text-xl mr-3 mt-1"></i>
                        <div class="flex-1">
                            <h4 class="font-semibold text-yellow-900 mb-1">Pixlet Not Available</h4>
                            <p class="text-sm text-yellow-800 mb-3">Pixlet is required to render Starlark apps. Click below to download and install Pixlet automatically.</p>
                            <button id="install-pixlet-btn" class="inline-flex items-center px-4 py-2 bg-yellow-600 hover:bg-yellow-700 text-white rounded-md text-sm font-medium transition-colors">
                                <i class="fas fa-download mr-2"></i>Download & Install Pixlet
                            </button>
                        </div>
                    </div>
                `;

                // Attach event listener to install button
                const installBtn = document.getElementById('install-pixlet-btn');
                if (installBtn) {
                    installBtn.addEventListener('click', installPixlet);
                }
            } else {
                // Get display info for magnification recommendation
                const displayInfo = data.display_info || {};
                const magnifyRec = displayInfo.calculated_magnify || 1;
                const displaySize = displayInfo.display_size || 'unknown';

                // Sanitize all dynamic values
                const safeVersion = sanitizeHtml(data.pixlet_version || 'Unknown');
                const safeInstalledApps = sanitizeHtml(data.installed_apps);
                const safeEnabledApps = sanitizeHtml(data.enabled_apps);
                const safeDisplaySize = sanitizeHtml(displaySize);
                const safeMagnifyRec = sanitizeHtml(magnifyRec);

                let magnifyHint = '';
                if (magnifyRec > 1) {
                    magnifyHint = `<div class="mt-2 text-xs text-blue-700 bg-blue-50 px-3 py-2 rounded border border-blue-200">
                        <i class="fas fa-lightbulb mr-1"></i>
                        <strong>Tip:</strong> Your ${safeDisplaySize} display works best with <strong>magnify=${safeMagnifyRec}</strong>.
                        Configure this in plugin settings for sharper output.
                    </div>`;
                }

                banner.className = 'mb-6 p-4 rounded-lg border border-green-400 bg-green-50';
                banner.innerHTML = `
                    <div class="flex items-center justify-between">
                        <div class="flex-1">
                            <div class="flex items-center">
                                <i class="fas fa-check-circle text-green-600 text-xl mr-3"></i>
                                <div>
                                    <h4 class="font-semibold text-green-900">Pixlet Ready</h4>
                                    <p class="text-sm text-green-800">Version: ${safeVersion} | ${safeInstalledApps} apps installed | ${safeEnabledApps} enabled</p>
                                </div>
                            </div>
                            ${magnifyHint}
                        </div>
                        ${data.plugin_enabled ? '<span class="text-xs bg-green-200 text-green-800 px-2 py-1 rounded ml-3">ENABLED</span>' : '<span class="text-xs bg-gray-200 text-gray-800 px-2 py-1 rounded ml-3">DISABLED</span>'}
                    </div>
                `;
            }
        } catch (error) {
            console.error('Error loading Starlark status:', error);
        }
    }

    async function loadStarlarkApps() {
        try {
            const response = await fetch('/api/v3/starlark/apps');
            const data = await response.json();

            if (data.status === 'error') {
                showNotification(data.message, 'error');
                return;
            }

            const grid = document.getElementById('starlark-apps-grid');
            const empty = document.getElementById('starlark-apps-empty');
            const count = document.getElementById('starlark-apps-count');

            if (!grid) return;

            // Update count
            if (count) {
                count.textContent = `${data.count} app${data.count !== 1 ? 's' : ''}`;
            }

            // Show empty state or apps grid
            if (data.count === 0) {
                grid.classList.add('hidden');
                if (empty) empty.classList.remove('hidden');
                return;
            }

            if (empty) empty.classList.add('hidden');
            grid.classList.remove('hidden');

            // Render apps
            grid.innerHTML = data.apps.map(app => renderAppCard(app)).join('');

            // Set up event delegation for app cards
            setupAppCardEventDelegation(grid);

        } catch (error) {
            console.error('Error loading Starlark apps:', error);
            showNotification('Failed to load apps', 'error');
        }
    }

    function renderAppCard(app) {
        const statusColor = app.enabled ? 'green' : 'gray';
        const statusIcon = app.enabled ? 'check-circle' : 'pause-circle';
        const hasFrames = app.has_frames ? '<i class="fas fa-film text-blue-500 text-xs"></i>' : '';

        // Sanitize all dynamic values
        const safeName = sanitizeHtml(app.name);
        const safeId = sanitizeHtml(app.id);
        const safeRenderInterval = sanitizeHtml(app.render_interval);
        const safeDisplayDuration = sanitizeHtml(app.display_duration);

        return `
            <div class="border border-gray-200 rounded-lg p-4 hover:shadow-lg transition-shadow bg-white" data-app-id="${safeId}">
                <div class="flex items-start justify-between mb-3">
                    <div class="flex-1">
                        <h4 class="font-semibold text-gray-900 text-sm mb-1 truncate" title="${safeName}">${safeName}</h4>
                        <p class="text-xs text-gray-500">${safeId}</p>
                    </div>
                    <div class="flex items-center gap-1">
                        ${hasFrames}
                        <i class="fas fa-${statusIcon} text-${statusColor}-500"></i>
                    </div>
                </div>

                <div class="text-xs text-gray-600 mb-3 space-y-1">
                    <div><span class="font-medium">Render:</span> ${safeRenderInterval}s</div>
                    <div><span class="font-medium">Display:</span> ${safeDisplayDuration}s</div>
                    ${app.has_schema ? '<div class="text-blue-600"><i class="fas fa-cog mr-1"></i>Configurable</div>' : ''}
                </div>

                <div class="flex flex-wrap gap-2">
                    <button data-action="toggle" data-enabled="${app.enabled}"
                            class="flex-1 text-xs px-3 py-1.5 ${app.enabled ? 'bg-gray-100 hover:bg-gray-200 text-gray-700' : 'bg-green-100 hover:bg-green-200 text-green-700'} rounded-md font-medium transition-colors">
                        ${app.enabled ? '<i class="fas fa-pause mr-1"></i>Disable' : '<i class="fas fa-play mr-1"></i>Enable'}
                    </button>
                    <button data-action="configure"
                            class="flex-1 text-xs px-3 py-1.5 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded-md font-medium transition-colors">
                        <i class="fas fa-cog mr-1"></i>Config
                    </button>
                    <button data-action="render"
                            class="flex-1 text-xs px-3 py-1.5 bg-purple-100 hover:bg-purple-200 text-purple-700 rounded-md font-medium transition-colors">
                        <i class="fas fa-sync mr-1"></i>Render
                    </button>
                    <button data-action="uninstall"
                            class="flex-1 text-xs px-3 py-1.5 bg-red-100 hover:bg-red-200 text-red-700 rounded-md font-medium transition-colors">
                        <i class="fas fa-trash mr-1"></i>Delete
                    </button>
                </div>
            </div>
        `;
    }

    /**
     * Set up event delegation for app card buttons.
     * Uses data attributes to avoid inline onclick handlers.
     */
    function setupAppCardEventDelegation(grid) {
        // Guard: only attach listener once per grid element
        if (gridsWithListeners.has(grid)) {
            return;
        }
        gridsWithListeners.add(grid);
        
        grid.addEventListener('click', async (e) => {
            const button = e.target.closest('button[data-action]');
            if (!button) return;

            const card = button.closest('[data-app-id]');
            if (!card) return;

            const appId = card.dataset.appId;
            const action = button.dataset.action;

            switch (action) {
                case 'toggle': {
                    const enabled = button.dataset.enabled === 'true';
                    await toggleStarlarkApp(appId, !enabled);
                    break;
                }
                case 'configure':
                    await configureStarlarkApp(appId);
                    break;
                case 'render':
                    await renderStarlarkApp(appId);
                    break;
                case 'uninstall':
                    await uninstallStarlarkApp(appId);
                    break;
            }
        });
    }

    function openUploadModal() {
        const modal = document.getElementById('upload-star-modal');
        if (modal) {
            modal.style.display = 'flex';
            // Reset form
            const form = document.getElementById('upload-star-form');
            if (form) form.reset();
            const fileName = document.getElementById('selected-file-name');
            if (fileName) fileName.classList.add('hidden');
        }
    }

    window.closeUploadModal = function() {
        const modal = document.getElementById('upload-star-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    };

    async function handleUploadSubmit(event) {
        event.preventDefault();

        const submitBtn = document.getElementById('upload-star-submit-btn');
        const originalText = submitBtn.innerHTML;

        try {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Uploading...';

            const formData = new FormData(event.target);

            const response = await fetch('/api/v3/starlark/upload', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message, 'success');
                window.closeUploadModal();
                loadStarlarkApps();
                loadStarlarkStatus();
            } else {
                showNotification(data.message, 'error');
            }

        } catch (error) {
            console.error('Error uploading app:', error);
            showNotification('Failed to upload app', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    }

    async function toggleStarlarkApp(appId, enabled) {
        try {
            const response = await fetch(`/api/v3/starlark/apps/${appId}/toggle`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled })
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message, 'success');
                loadStarlarkApps();
                loadStarlarkStatus();
            } else {
                showNotification(data.message, 'error');
            }
        } catch (error) {
            console.error('Error toggling app:', error);
            showNotification('Failed to toggle app', 'error');
        }
    }

    async function renderStarlarkApp(appId) {
        try {
            showNotification('Rendering app...', 'info');

            const response = await fetch(`/api/v3/starlark/apps/${appId}/render`, {
                method: 'POST'
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message + ` (${data.frame_count} frames)`, 'success');
                loadStarlarkApps();
            } else {
                showNotification(data.message, 'error');
            }
        } catch (error) {
            console.error('Error rendering app:', error);
            showNotification('Failed to render app', 'error');
        }
    }

    async function configureStarlarkApp(appId) {
        try {
            currentConfigAppId = appId;

            const response = await fetch(`/api/v3/starlark/apps/${appId}`);
            const data = await response.json();

            if (data.status === 'error') {
                showNotification(data.message, 'error');
                return;
            }

            const app = data.app;

            // Update modal title (textContent automatically escapes HTML)
            document.getElementById('config-app-name').textContent = app.name || '';

            // Generate config fields
            const fieldsContainer = document.getElementById('starlark-config-fields');

            if (!app.schema || Object.keys(app.schema).length === 0) {
                fieldsContainer.innerHTML = `
                    <div class="text-center py-8 text-gray-500">
                        <i class="fas fa-info-circle text-4xl mb-3"></i>
                        <p>This app has no configurable settings.</p>
                    </div>
                `;
            } else {
                fieldsContainer.innerHTML = generateConfigFields(app.schema, app.config);
            }

            // Show modal
            const configModal = document.getElementById('starlark-config-modal');
            if (configModal) configModal.style.display = 'flex';

        } catch (error) {
            console.error('Error loading app config:', error);
            showNotification('Failed to load configuration', 'error');
        }
    }

    function generateConfigFields(schema, config) {
        // Simple field generator - can be enhanced to handle complex Pixlet schemas
        let html = '';

        for (const [key, field] of Object.entries(schema)) {
            const value = config[key] || field.default || '';
            const type = field.type || 'string';

            // Sanitize all dynamic values
            const safeKey = sanitizeHtml(key);
            const safeName = sanitizeHtml(field.name || key);
            const safeDescription = sanitizeHtml(field.description || '');
            const safeValue = sanitizeHtml(value);
            const safePlaceholder = sanitizeHtml(field.placeholder || '');

            html += `
                <div>
                    <label for="config-${safeKey}" class="block text-sm font-medium text-gray-700 mb-2">
                        ${safeName}
                        ${field.required ? '<span class="text-red-500">*</span>' : ''}
                    </label>
                    ${field.description ? `<p class="text-xs text-gray-500 mb-2">${safeDescription}</p>` : ''}
            `;

            if (type === 'bool' || type === 'boolean') {
                html += `
                    <label class="flex items-center cursor-pointer">
                        <input type="checkbox" name="${safeKey}" id="config-${safeKey}"
                               ${value ? 'checked' : ''}
                               class="form-checkbox h-5 w-5 text-blue-600 rounded">
                        <span class="ml-2 text-sm text-gray-700">Enable ${safeName}</span>
                    </label>
                `;
            } else if (field.options) {
                html += `
                    <select name="${safeKey}" id="config-${safeKey}"
                            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                `;
                for (const option of field.options) {
                    const safeOption = sanitizeHtml(option);
                    // Compare sanitized values for safety
                    const safeValueForCompare = sanitizeHtml(String(value));
                    html += `<option value="${safeOption}" ${safeValueForCompare === safeOption ? 'selected' : ''}>${safeOption}</option>`;
                }
                html += '</select>';
            } else {
                html += `
                    <input type="text" name="${safeKey}" id="config-${safeKey}"
                           value="${safeValue}"
                           ${field.required ? 'required' : ''}
                           placeholder="${safePlaceholder}"
                           class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                `;
            }

            html += '</div>';
        }

        return html;
    }

    window.closeConfigModal = function() {
        const modal = document.getElementById('starlark-config-modal');
        if (modal) modal.style.display = 'none';
        currentConfigAppId = null;
    };

    async function handleConfigSubmit(event) {
        event.preventDefault();

        if (!currentConfigAppId) return;

        const submitBtn = document.getElementById('save-starlark-config-btn');
        const originalText = submitBtn.innerHTML;

        try {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Saving...';

            const formData = new FormData(event.target);
            const config = {};

            for (const [key, value] of formData.entries()) {
                // Handle checkboxes
                const input = event.target.elements[key];
                if (input && input.type === 'checkbox') {
                    config[key] = input.checked;
                } else {
                    config[key] = value;
                }
            }

            const response = await fetch(`/api/v3/starlark/apps/${currentConfigAppId}/config`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message, 'success');
                window.closeConfigModal();
                loadStarlarkApps();
            } else {
                showNotification(data.message, 'error');
            }

        } catch (error) {
            console.error('Error saving config:', error);
            showNotification('Failed to save configuration', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    }

    async function uninstallStarlarkApp(appId) {
        if (!confirm(`Are you sure you want to uninstall this app? This cannot be undone.`)) {
            return;
        }

        try {
            const response = await fetch(`/api/v3/starlark/apps/${appId}`, {
                method: 'DELETE'
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message, 'success');
                loadStarlarkApps();
                loadStarlarkStatus();
            } else {
                showNotification(data.message, 'error');
            }
        } catch (error) {
            console.error('Error uninstalling app:', error);
            showNotification('Failed to uninstall app', 'error');
        }
    }

    // Utility function for notifications (assuming it exists in the main app)
    function showNotification(message, type) {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
        } else {
            console.log(`[${type.toUpperCase()}] ${message}`);
        }
    }

    // ========================================================================
    // Repository Browser Functions
    // ========================================================================

    function setupRepositoryListeners() {
        const browseBtn = document.getElementById('browse-repository-btn');
        console.log('[Starlark] Browse button found:', !!browseBtn);
        if (browseBtn) {
            browseBtn.addEventListener('click', openRepositoryBrowser);
            console.log('[Starlark] Browse button event listener attached');
        }

        const applyFiltersBtn = document.getElementById('repo-apply-filters-btn');
        if (applyFiltersBtn) {
            applyFiltersBtn.addEventListener('click', applyRepositoryFilters);
        }

        const searchInput = document.getElementById('repo-search-input');
        if (searchInput) {
            searchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    applyRepositoryFilters();
                }
            });
        }
    }

    function openRepositoryBrowser() {
        console.log('[Starlark] openRepositoryBrowser called');
        const modal = document.getElementById('repository-browser-modal');
        console.log('[Starlark] Modal found:', !!modal);
        if (!modal) return;

        modal.style.display = 'flex';

        // Load categories first
        loadRepositoryCategories();

        // Then load apps
        loadRepositoryApps();
    }

    window.closeRepositoryBrowser = function() {
        const modal = document.getElementById('repository-browser-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    };

    async function loadRepositoryCategories() {
        try {
            const response = await fetch('/api/v3/starlark/repository/categories');
            const data = await response.json();

            if (data.status === 'success') {
                repositoryCategories = data.categories;

                const select = document.getElementById('repo-category-filter');
                if (select) {
                    // Keep "All Categories" option
                    select.innerHTML = '<option value="all">All Categories</option>';

                    // Add category options
                    repositoryCategories.forEach(category => {
                        const option = document.createElement('option');
                        option.value = category;
                        option.textContent = category.charAt(0).toUpperCase() + category.slice(1);
                        select.appendChild(option);
                    });
                }
            }
        } catch (error) {
            console.error('Error loading categories:', error);
        }
    }

    async function loadRepositoryApps(search = '', category = 'all') {
        const loading = document.getElementById('repo-apps-loading');
        const grid = document.getElementById('repo-apps-grid');
        const empty = document.getElementById('repo-apps-empty');

        if (loading) loading.classList.remove('hidden');
        if (grid) grid.classList.add('hidden');
        if (empty) empty.classList.add('hidden');

        try {
            const params = new URLSearchParams({ limit: 100 });
            if (search) params.append('search', search);
            if (category && category !== 'all') params.append('category', category);

            const response = await fetch(`/api/v3/starlark/repository/browse?${params}`);
            const data = await response.json();

            if (data.status === 'error') {
                showNotification(data.message, 'error');
                if (loading) loading.classList.add('hidden');
                // Show error state in the modal
                if (empty) {
                    empty.innerHTML = `
                        <i class="fas fa-exclamation-circle text-6xl text-red-300 mb-4"></i>
                        <h3 class="text-lg font-semibold text-gray-700 mb-2">Unable to Load Repository</h3>
                        <p class="text-gray-500">${sanitizeHtml(data.message)}</p>
                    `;
                    empty.classList.remove('hidden');
                }
                return;
            }

            repositoryApps = data.apps || [];

            // Update rate limit info
            updateRateLimitInfo(data.rate_limit);

            // Hide loading
            if (loading) loading.classList.add('hidden');

            // Show apps or empty state
            if (repositoryApps.length === 0) {
                if (empty) empty.classList.remove('hidden');
            } else {
                if (grid) {
                    grid.innerHTML = repositoryApps.map(app => renderRepositoryAppCard(app)).join('');
                    grid.classList.remove('hidden');
                    // Set up event delegation for repository app cards
                    setupRepositoryAppEventDelegation(grid);
                }
            }

        } catch (error) {
            console.error('Error loading repository apps:', error);
            showNotification('Failed to load repository apps', 'error');
            if (loading) loading.classList.add('hidden');
        }
    }

    function renderRepositoryAppCard(app) {
        const name = app.name || app.id.replace('_', ' ').replace('-', ' ');
        const summary = app.summary || app.desc || 'No description available';
        const author = app.author || 'Community';
        const category = app.category || 'Other';

        // Sanitize all dynamic values
        const safeName = sanitizeHtml(name);
        const safeId = sanitizeHtml(app.id);
        const safeSummary = sanitizeHtml(summary);
        const safeAuthor = sanitizeHtml(author);
        const safeCategory = sanitizeHtml(category);

        return `
            <div class="border border-gray-200 rounded-lg p-4 hover:shadow-lg transition-shadow bg-white" data-repo-app-id="${safeId}">
                <div class="mb-3">
                    <h4 class="font-semibold text-gray-900 text-sm mb-1">${safeName}</h4>
                    <p class="text-xs text-gray-600 line-clamp-2 mb-2">${safeSummary}</p>
                    <div class="flex items-center gap-2 text-xs text-gray-500">
                        <span><i class="fas fa-user mr-1"></i>${safeAuthor}</span>
                        <span>•</span>
                        <span><i class="fas fa-tag mr-1"></i>${safeCategory}</span>
                    </div>
                </div>

                <button data-action="install"
                        class="w-full text-sm px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-md font-medium transition-colors">
                    <i class="fas fa-download mr-2"></i>Install
                </button>
            </div>
        `;
    }

    /**
     * Set up event delegation for repository app install buttons.
     * Uses data attributes to avoid inline onclick handlers.
     */
    function setupRepositoryAppEventDelegation(grid) {
        // Guard: only attach listener once per grid element
        if (repoGridsWithListeners.has(grid)) {
            return;
        }
        repoGridsWithListeners.add(grid);
        
        grid.addEventListener('click', async (e) => {
            const button = e.target.closest('button[data-action="install"]');
            if (!button) return;

            const card = button.closest('[data-repo-app-id]');
            if (!card) return;

            const appId = card.dataset.repoAppId;
            await installFromRepository(appId);
        });
    }

    function updateRateLimitInfo(rateLimit) {
        const info = document.getElementById('repo-rate-limit-info');
        if (!info || !rateLimit) return;

        const remaining = rateLimit.remaining || 0;
        const limit = rateLimit.limit || 0;
        const used = rateLimit.used || 0;

        // Sanitize numeric values
        const safeRemaining = sanitizeHtml(remaining);
        const safeLimit = sanitizeHtml(limit);

        let color = 'text-green-600';
        if (remaining < limit * 0.3) color = 'text-yellow-600';
        if (remaining < limit * 0.1) color = 'text-red-600';

        info.innerHTML = `
            <i class="fab fa-github mr-1"></i>
            GitHub API: <span class="${color} font-medium">${safeRemaining}/${safeLimit}</span> requests remaining
        `;
    }

    function applyRepositoryFilters() {
        const search = document.getElementById('repo-search-input')?.value || '';
        const category = document.getElementById('repo-category-filter')?.value || 'all';

        loadRepositoryApps(search, category);
    }

    async function installFromRepository(appId) {
        try {
            showNotification(`Installing ${sanitizeHtml(appId)}...`, 'info');

            const response = await fetch('/api/v3/starlark/repository/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ app_id: appId })
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message, 'success');

                // Close repository browser
                window.closeRepositoryBrowser();

                // Refresh installed apps
                loadStarlarkApps();
                loadStarlarkStatus();
            } else {
                showNotification(data.message, 'error');
            }

        } catch (error) {
            console.error('Error installing from repository:', error);
            showNotification('Failed to install app', 'error');
        }
    }

    // ========================================================================
    // Pixlet Installation Function
    // ========================================================================

    async function installPixlet() {
        const btn = document.getElementById('install-pixlet-btn');
        if (!btn) return;

        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Downloading Pixlet...';

        try {
            showNotification('Downloading Pixlet binary...', 'info');

            const response = await fetch('/api/v3/starlark/install-pixlet', {
                method: 'POST'
            });

            const data = await response.json();

            if (data.status === 'success') {
                showNotification(data.message, 'success');
                // Refresh status to show Pixlet is now available
                setTimeout(() => loadStarlarkStatus(), 1000);
            } else {
                showNotification(data.message || 'Failed to install Pixlet', 'error');
                btn.disabled = false;
                btn.innerHTML = originalText;
            }

        } catch (error) {
            console.error('Error installing Pixlet:', error);
            showNotification('Failed to download Pixlet. Please check your internet connection.', 'error');
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

})();
