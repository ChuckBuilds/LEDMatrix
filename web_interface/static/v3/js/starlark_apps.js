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

    // Initialize on page load
    document.addEventListener('DOMContentLoaded', function() {
        initStarlarkApps();
    });

    function initStarlarkApps() {
        // Set up event listeners
        setupEventListeners();

        // Load initial data
        loadStarlarkStatus();
        loadStarlarkApps();
    }

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
                    fileInput.files = files;
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
        try {
            const response = await fetch('/api/v3/starlark/status');
            const data = await response.json();

            const banner = document.getElementById('pixlet-status-banner');
            if (!banner) return;

            if (data.status === 'error' || !data.pixlet_available) {
                banner.className = 'mb-6 p-4 rounded-lg border border-yellow-400 bg-yellow-50';
                banner.innerHTML = `
                    <div class="flex items-start">
                        <i class="fas fa-exclamation-triangle text-yellow-600 text-xl mr-3 mt-1"></i>
                        <div>
                            <h4 class="font-semibold text-yellow-900 mb-1">Pixlet Not Available</h4>
                            <p class="text-sm text-yellow-800">Pixlet is required to render Starlark apps. Please install Pixlet or run <code class="bg-yellow-100 px-2 py-1 rounded">./scripts/download_pixlet.sh</code></p>
                        </div>
                    </div>
                `;
            } else {
                // Get display info for magnification recommendation
                const displayInfo = data.display_info || {};
                const magnifyRec = displayInfo.calculated_magnify || 1;
                const displaySize = displayInfo.display_size || 'unknown';

                let magnifyHint = '';
                if (magnifyRec > 1) {
                    magnifyHint = `<div class="mt-2 text-xs text-blue-700 bg-blue-50 px-3 py-2 rounded border border-blue-200">
                        <i class="fas fa-lightbulb mr-1"></i>
                        <strong>Tip:</strong> Your ${displaySize} display works best with <strong>magnify=${magnifyRec}</strong>.
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
                                    <p class="text-sm text-green-800">Version: ${data.pixlet_version || 'Unknown'} | ${data.installed_apps} apps installed | ${data.enabled_apps} enabled</p>
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

        } catch (error) {
            console.error('Error loading Starlark apps:', error);
            showNotification('Failed to load apps', 'error');
        }
    }

    function renderAppCard(app) {
        const statusColor = app.enabled ? 'green' : 'gray';
        const statusIcon = app.enabled ? 'check-circle' : 'pause-circle';
        const hasFrames = app.has_frames ? '<i class="fas fa-film text-blue-500 text-xs"></i>' : '';

        return `
            <div class="border border-gray-200 rounded-lg p-4 hover:shadow-lg transition-shadow bg-white">
                <div class="flex items-start justify-between mb-3">
                    <div class="flex-1">
                        <h4 class="font-semibold text-gray-900 text-sm mb-1 truncate" title="${app.name}">${app.name}</h4>
                        <p class="text-xs text-gray-500">${app.id}</p>
                    </div>
                    <div class="flex items-center gap-1">
                        ${hasFrames}
                        <i class="fas fa-${statusIcon} text-${statusColor}-500"></i>
                    </div>
                </div>

                <div class="text-xs text-gray-600 mb-3 space-y-1">
                    <div><span class="font-medium">Render:</span> ${app.render_interval}s</div>
                    <div><span class="font-medium">Display:</span> ${app.display_duration}s</div>
                    ${app.has_schema ? '<div class="text-blue-600"><i class="fas fa-cog mr-1"></i>Configurable</div>' : ''}
                </div>

                <div class="flex flex-wrap gap-2">
                    <button onclick="toggleStarlarkApp('${app.id}', ${!app.enabled})"
                            class="flex-1 text-xs px-3 py-1.5 ${app.enabled ? 'bg-gray-100 hover:bg-gray-200 text-gray-700' : 'bg-green-100 hover:bg-green-200 text-green-700'} rounded-md font-medium transition-colors">
                        ${app.enabled ? '<i class="fas fa-pause mr-1"></i>Disable' : '<i class="fas fa-play mr-1"></i>Enable'}
                    </button>
                    <button onclick="configureStarlarkApp('${app.id}')"
                            class="flex-1 text-xs px-3 py-1.5 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded-md font-medium transition-colors">
                        <i class="fas fa-cog mr-1"></i>Config
                    </button>
                    <button onclick="renderStarlarkApp('${app.id}')"
                            class="flex-1 text-xs px-3 py-1.5 bg-purple-100 hover:bg-purple-200 text-purple-700 rounded-md font-medium transition-colors">
                        <i class="fas fa-sync mr-1"></i>Render
                    </button>
                    <button onclick="uninstallStarlarkApp('${app.id}')"
                            class="flex-1 text-xs px-3 py-1.5 bg-red-100 hover:bg-red-200 text-red-700 rounded-md font-medium transition-colors">
                        <i class="fas fa-trash mr-1"></i>Delete
                    </button>
                </div>
            </div>
        `;
    }

    function openUploadModal() {
        const modal = document.getElementById('upload-star-modal');
        if (modal) {
            modal.classList.remove('hidden');
            // Reset form
            document.getElementById('upload-star-form').reset();
            document.getElementById('selected-file-name').classList.add('hidden');
        }
    }

    window.closeUploadModal = function() {
        const modal = document.getElementById('upload-star-modal');
        if (modal) {
            modal.classList.add('hidden');
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

    window.toggleStarlarkApp = async function(appId, enabled) {
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
    };

    window.renderStarlarkApp = async function(appId) {
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
    };

    window.configureStarlarkApp = async function(appId) {
        try {
            currentConfigAppId = appId;

            const response = await fetch(`/api/v3/starlark/apps/${appId}`);
            const data = await response.json();

            if (data.status === 'error') {
                showNotification(data.message, 'error');
                return;
            }

            const app = data.app;

            // Update modal title
            document.getElementById('config-app-name').textContent = app.name;

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
            document.getElementById('starlark-config-modal').classList.remove('hidden');

        } catch (error) {
            console.error('Error loading app config:', error);
            showNotification('Failed to load configuration', 'error');
        }
    };

    function generateConfigFields(schema, config) {
        // Simple field generator - can be enhanced to handle complex Pixlet schemas
        let html = '';

        for (const [key, field] of Object.entries(schema)) {
            const value = config[key] || field.default || '';
            const type = field.type || 'string';

            html += `
                <div>
                    <label for="config-${key}" class="block text-sm font-medium text-gray-700 mb-2">
                        ${field.name || key}
                        ${field.required ? '<span class="text-red-500">*</span>' : ''}
                    </label>
                    ${field.description ? `<p class="text-xs text-gray-500 mb-2">${field.description}</p>` : ''}
            `;

            if (type === 'bool' || type === 'boolean') {
                html += `
                    <label class="flex items-center cursor-pointer">
                        <input type="checkbox" name="${key}" id="config-${key}"
                               ${value ? 'checked' : ''}
                               class="form-checkbox h-5 w-5 text-blue-600 rounded">
                        <span class="ml-2 text-sm text-gray-700">Enable ${field.name || key}</span>
                    </label>
                `;
            } else if (field.options) {
                html += `
                    <select name="${key}" id="config-${key}"
                            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                `;
                for (const option of field.options) {
                    html += `<option value="${option}" ${value === option ? 'selected' : ''}>${option}</option>`;
                }
                html += '</select>';
            } else {
                html += `
                    <input type="text" name="${key}" id="config-${key}"
                           value="${value}"
                           ${field.required ? 'required' : ''}
                           placeholder="${field.placeholder || ''}"
                           class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                `;
            }

            html += '</div>';
        }

        return html;
    }

    window.closeConfigModal = function() {
        document.getElementById('starlark-config-modal').classList.add('hidden');
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

    window.uninstallStarlarkApp = async function(appId) {
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
    };

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
        if (browseBtn) {
            browseBtn.addEventListener('click', openRepositoryBrowser);
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
        const modal = document.getElementById('repository-browser-modal');
        if (!modal) return;

        modal.classList.remove('hidden');

        // Load categories first
        loadRepositoryCategories();

        // Then load apps
        loadRepositoryApps();
    }

    window.closeRepositoryBrowser = function() {
        const modal = document.getElementById('repository-browser-modal');
        if (modal) {
            modal.classList.add('hidden');
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
                return;
            }

            repositoryApps = data.apps;

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

        return `
            <div class="border border-gray-200 rounded-lg p-4 hover:shadow-lg transition-shadow bg-white">
                <div class="mb-3">
                    <h4 class="font-semibold text-gray-900 text-sm mb-1">${name}</h4>
                    <p class="text-xs text-gray-600 line-clamp-2 mb-2">${summary}</p>
                    <div class="flex items-center gap-2 text-xs text-gray-500">
                        <span><i class="fas fa-user mr-1"></i>${author}</span>
                        <span>•</span>
                        <span><i class="fas fa-tag mr-1"></i>${category}</span>
                    </div>
                </div>

                <button onclick="installFromRepository('${app.id}')"
                        class="w-full text-sm px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-md font-medium transition-colors">
                    <i class="fas fa-download mr-2"></i>Install
                </button>
            </div>
        `;
    }

    function updateRateLimitInfo(rateLimit) {
        const info = document.getElementById('repo-rate-limit-info');
        if (!info || !rateLimit) return;

        const remaining = rateLimit.remaining || 0;
        const limit = rateLimit.limit || 0;
        const used = rateLimit.used || 0;

        let color = 'text-green-600';
        if (remaining < limit * 0.3) color = 'text-yellow-600';
        if (remaining < limit * 0.1) color = 'text-red-600';

        info.innerHTML = `
            <i class="fab fa-github mr-1"></i>
            GitHub API: <span class="${color} font-medium">${remaining}/${limit}</span> requests remaining
        `;
    }

    function applyRepositoryFilters() {
        const search = document.getElementById('repo-search-input')?.value || '';
        const category = document.getElementById('repo-category-filter')?.value || 'all';

        loadRepositoryApps(search, category);
    }

    window.installFromRepository = async function(appId) {
        try {
            showNotification(`Installing ${appId}...`, 'info');

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
    };

    // Initialize repository listeners when document loads
    document.addEventListener('DOMContentLoaded', function() {
        setupRepositoryListeners();
    });

})();
