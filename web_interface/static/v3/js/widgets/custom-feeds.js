/**
 * Custom Feeds Widget
 * 
 * Handles table-based RSS feed editor with logo uploads.
 * Allows adding, removing, and editing custom RSS feed entries.
 * 
 * @module CustomFeedsWidget
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[CustomFeedsWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    /**
     * Register the custom-feeds widget
     */
    window.LEDMatrixWidgets.register('custom-feeds', {
        name: 'Custom Feeds Widget',
        version: '1.0.0',
        
        /**
         * Render the custom feeds widget
         * Note: This widget is currently server-side rendered via Jinja2 template.
         * This registration ensures the handlers are available globally.
         */
        render: function(container, config, value, options) {
            // For now, widgets are server-side rendered
            // This function is a placeholder for future client-side rendering
            console.log('[CustomFeedsWidget] Render called (server-side rendered)');
        },
        
        /**
         * Get current value from widget
         * @param {string} fieldId - Field ID
         * @returns {Array} Array of feed objects
         */
        getValue: function(fieldId) {
            const tbody = document.getElementById(`${fieldId}_tbody`);
            if (!tbody) return [];
            
            const rows = tbody.querySelectorAll('.custom-feed-row');
            const feeds = [];
            
            rows.forEach((row, index) => {
                const nameInput = row.querySelector('input[name*=".name"]');
                const urlInput = row.querySelector('input[name*=".url"]');
                const enabledInput = row.querySelector('input[name*=".enabled"]');
                const logoPathInput = row.querySelector('input[name*=".logo.path"]');
                const logoIdInput = row.querySelector('input[name*=".logo.id"]');
                
                if (nameInput && urlInput) {
                    feeds.push({
                        name: nameInput.value,
                        url: urlInput.value,
                        enabled: enabledInput ? enabledInput.checked : true,
                        logo: logoPathInput || logoIdInput ? {
                            path: logoPathInput ? logoPathInput.value : '',
                            id: logoIdInput ? logoIdInput.value : ''
                        } : null
                    });
                }
            });
            
            return feeds;
        },
        
        /**
         * Set value in widget
         * @param {string} fieldId - Field ID
         * @param {Array} feeds - Array of feed objects
         */
        setValue: function(fieldId, feeds) {
            if (!Array.isArray(feeds)) {
                console.error('[CustomFeedsWidget] setValue expects an array');
                return;
            }
            
            // Clear existing rows
            const tbody = document.getElementById(`${fieldId}_tbody`);
            if (tbody) {
                tbody.innerHTML = '';
            }
            
            // Add rows for each feed
            feeds.forEach((feed, index) => {
                // This would need the fullKey and pluginId from options
                // For now, this is a placeholder
                console.log('[CustomFeedsWidget] setValue called - full implementation requires template context');
            });
        },
        
        handlers: {
            // Handlers are attached to window for backwards compatibility
        }
    });

    /**
     * Add a new custom feed row to the table
     * @param {string} fieldId - Field ID
     * @param {string} fullKey - Full field key (e.g., "feeds.custom_feeds")
     * @param {number} maxItems - Maximum number of items allowed
     * @param {string} pluginId - Plugin ID
     */
    window.addCustomFeedRow = function(fieldId, fullKey, maxItems, pluginId) {
        const tbody = document.getElementById(fieldId + '_tbody');
        if (!tbody) return;
        
        const currentRows = tbody.querySelectorAll('.custom-feed-row');
        if (currentRows.length >= maxItems) {
            const notifyFn = window.showNotification || alert;
            notifyFn(`Maximum ${maxItems} feeds allowed`, 'error');
            return;
        }
        
        const newIndex = currentRows.length;
        const newRow = document.createElement('tr');
        newRow.className = 'custom-feed-row';
        newRow.setAttribute('data-index', newIndex);
        
        // Create name cell
        const nameCell = document.createElement('td');
        nameCell.className = 'px-4 py-3 whitespace-nowrap';
        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.name = `${fullKey}.${newIndex}.name`;
        nameInput.value = '';
        nameInput.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
        nameInput.placeholder = 'Feed Name';
        nameInput.required = true;
        nameCell.appendChild(nameInput);
        
        // Create URL cell
        const urlCell = document.createElement('td');
        urlCell.className = 'px-4 py-3 whitespace-nowrap';
        const urlInput = document.createElement('input');
        urlInput.type = 'url';
        urlInput.name = `${fullKey}.${newIndex}.url`;
        urlInput.value = '';
        urlInput.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
        urlInput.placeholder = 'https://example.com/feed';
        urlInput.required = true;
        urlCell.appendChild(urlInput);
        
        // Create logo cell
        const logoCell = document.createElement('td');
        logoCell.className = 'px-4 py-3 whitespace-nowrap';
        const logoContainer = document.createElement('div');
        logoContainer.className = 'flex items-center space-x-2';
        
        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.id = `${fieldId}_logo_${newIndex}`;
        fileInput.accept = 'image/png,image/jpeg,image/bmp,image/gif';
        fileInput.style.display = 'none';
        fileInput.dataset.index = String(newIndex);
        fileInput.addEventListener('change', function(e) {
            const idx = parseInt(e.target.dataset.index || '0', 10);
            handleCustomFeedLogoUpload(e, fieldId, idx, pluginId, fullKey);
        });
        
        const uploadButton = document.createElement('button');
        uploadButton.type = 'button';
        uploadButton.className = 'px-2 py-1 text-xs bg-gray-200 hover:bg-gray-300 rounded';
        uploadButton.addEventListener('click', function() {
            fileInput.click();
        });
        const uploadIcon = document.createElement('i');
        uploadIcon.className = 'fas fa-upload mr-1';
        uploadButton.appendChild(uploadIcon);
        uploadButton.appendChild(document.createTextNode(' Upload'));
        
        const noLogoSpan = document.createElement('span');
        noLogoSpan.className = 'text-xs text-gray-400';
        noLogoSpan.textContent = 'No logo';
        
        logoContainer.appendChild(fileInput);
        logoContainer.appendChild(uploadButton);
        logoContainer.appendChild(noLogoSpan);
        logoCell.appendChild(logoContainer);
        
        // Create enabled cell
        const enabledCell = document.createElement('td');
        enabledCell.className = 'px-4 py-3 whitespace-nowrap text-center';
        const enabledInput = document.createElement('input');
        enabledInput.type = 'checkbox';
        enabledInput.name = `${fullKey}.${newIndex}.enabled`;
        enabledInput.checked = true;
        enabledInput.value = 'true';
        enabledInput.className = 'h-4 w-4 text-blue-600';
        enabledCell.appendChild(enabledInput);
        
        // Create remove cell
        const removeCell = document.createElement('td');
        removeCell.className = 'px-4 py-3 whitespace-nowrap text-center';
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'text-red-600 hover:text-red-800 px-2 py-1';
        removeButton.addEventListener('click', function() {
            removeCustomFeedRow(this);
        });
        const removeIcon = document.createElement('i');
        removeIcon.className = 'fas fa-trash';
        removeButton.appendChild(removeIcon);
        removeCell.appendChild(removeButton);
        
        // Append all cells to row
        newRow.appendChild(nameCell);
        newRow.appendChild(urlCell);
        newRow.appendChild(logoCell);
        newRow.appendChild(enabledCell);
        newRow.appendChild(removeCell);
        tbody.appendChild(newRow);
    };
    
    /**
     * Remove a custom feed row from the table
     * @param {HTMLElement} button - The remove button element
     */
    window.removeCustomFeedRow = function(button) {
        const row = button.closest('tr');
        if (!row) return;
        
        if (confirm('Remove this feed?')) {
            const tbody = row.parentElement;
            if (!tbody) return;
            
            row.remove();
            
            // Re-index remaining rows
            const rows = tbody.querySelectorAll('.custom-feed-row');
            rows.forEach((r, index) => {
                const oldIndex = r.getAttribute('data-index');
                r.setAttribute('data-index', index);
                // Update all input names with new index
                r.querySelectorAll('input, button').forEach(input => {
                    const name = input.getAttribute('name');
                    if (name) {
                        // Replace pattern like "feeds.custom_feeds.0.name" with "feeds.custom_feeds.1.name"
                        input.setAttribute('name', name.replace(/\.\d+\./, `.${index}.`));
                    }
                    const id = input.id;
                    if (id) {
                        // Keep IDs aligned after reindex
                        input.id = id
                            .replace(/_logo_preview_\d+$/, `_logo_preview_${index}`)
                            .replace(/_logo_\d+$/, `_logo_${index}`);
                    }
                    // Keep dataset index aligned
                    if (input.dataset && 'index' in input.dataset) {
                        input.dataset.index = String(index);
                    }
                });
            });
        }
    };
    
    /**
     * Handle custom feed logo upload
     * @param {Event} event - File input change event
     * @param {string} fieldId - Field ID
     * @param {number} index - Feed row index
     * @param {string} pluginId - Plugin ID
     * @param {string} fullKey - Full field key
     */
    window.handleCustomFeedLogoUpload = function(event, fieldId, index, pluginId, fullKey) {
        const file = event.target.files[0];
        if (!file) return;
        
        const formData = new FormData();
        formData.append('file', file);
        formData.append('plugin_id', pluginId);
        
        fetch('/api/v3/plugins/assets/upload', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            // Check HTTP status before parsing JSON
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`Upload failed: ${response.status} ${response.statusText}${text ? ': ' + text : ''}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success' && data.data && data.data.files && data.data.files.length > 0) {
                const uploadedFile = data.data.files[0];
                const row = document.querySelector(`#${fieldId}_tbody tr[data-index="${index}"]`);
                if (row) {
                    const logoCell = row.querySelector('td:nth-child(3)');
                    const existingPathInput = logoCell.querySelector('input[name*=".logo.path"]');
                    const existingIdInput = logoCell.querySelector('input[name*=".logo.id"]');
                    const pathName = existingPathInput ? existingPathInput.name : `${fullKey}.${index}.logo.path`;
                    const idName = existingIdInput ? existingIdInput.name : `${fullKey}.${index}.logo.id`;
                    
                    // Normalize path: remove leading slashes, then add single leading slash
                    const normalizedPath = String(uploadedFile.path || '').replace(/^\/+/, '');
                    const imageSrc = '/' + normalizedPath;
                    
                    // Clear logoCell and build DOM safely to prevent XSS
                    logoCell.textContent = ''; // Clear existing content
                    
                    // Create container div
                    const container = document.createElement('div');
                    container.className = 'flex items-center space-x-2';
                    
                    // Create file input
                    const fileInput = document.createElement('input');
                    fileInput.type = 'file';
                    fileInput.id = `${fieldId}_logo_${index}`;
                    fileInput.accept = 'image/png,image/jpeg,image/bmp,image/gif';
                    fileInput.style.display = 'none';
                    fileInput.dataset.index = String(index);
                    fileInput.addEventListener('change', function(e) {
                        const idx = parseInt(e.target.dataset.index || '0', 10);
                        handleCustomFeedLogoUpload(e, fieldId, idx, pluginId, fullKey);
                    });
                    
                    // Create upload button
                    const uploadButton = document.createElement('button');
                    uploadButton.type = 'button';
                    uploadButton.className = 'px-2 py-1 text-xs bg-gray-200 hover:bg-gray-300 rounded';
                    uploadButton.addEventListener('click', function() {
                        fileInput.click();
                    });
                    const uploadIcon = document.createElement('i');
                    uploadIcon.className = 'fas fa-upload mr-1';
                    uploadButton.appendChild(uploadIcon);
                    uploadButton.appendChild(document.createTextNode(' Upload'));
                    
                    // Create img element
                    const img = document.createElement('img');
                    img.src = imageSrc;
                    img.alt = 'Logo';
                    img.className = 'w-8 h-8 object-cover rounded border';
                    img.id = `${fieldId}_logo_preview_${index}`;
                    
                    // Create hidden input for path
                    const pathInput = document.createElement('input');
                    pathInput.type = 'hidden';
                    pathInput.name = pathName;
                    pathInput.value = imageSrc;
                    
                    // Create hidden input for id
                    const idInput = document.createElement('input');
                    idInput.type = 'hidden';
                    idInput.name = idName;
                    idInput.value = String(uploadedFile.id);
                    
                    // Append all elements to container
                    container.appendChild(fileInput);
                    container.appendChild(uploadButton);
                    container.appendChild(img);
                    container.appendChild(pathInput);
                    container.appendChild(idInput);
                    
                    // Append container to logoCell
                    logoCell.appendChild(container);
                }
                // Allow re-uploading the same file
                event.target.value = '';
            } else {
                const notifyFn = window.showNotification || alert;
                notifyFn('Upload failed: ' + (data.message || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            console.error('Upload error:', error);
            const notifyFn = window.showNotification || alert;
            notifyFn('Upload failed: ' + error.message, 'error');
        });
    };

    console.log('[CustomFeedsWidget] Custom feeds widget registered');
})();
