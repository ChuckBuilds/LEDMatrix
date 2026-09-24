/**
 * File Upload Widget
 * 
 * Handles file uploads (primarily images) with drag-and-drop support,
 * preview, delete, and scheduling functionality.
 * 
 * @module FileUploadWidget
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[FileUploadWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    /**
     * Register the file-upload widget
     */
    window.LEDMatrixWidgets.register('file-upload', {
        name: 'File Upload Widget',
        version: '1.0.0',
        
        /**
         * No-op: plugin_config.html renders this widget server-side. The
         * registration exists for getValue/setValue and the window.* handlers
         * below.
         */
        render: function() {},
        
        /**
         * Get current value from widget
         * @param {string} fieldId - Field ID
         * @returns {Array} Array of uploaded files
         */
        getValue: function(fieldId) {
            return window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
        },
        
        /**
         * Set value in widget
         * @param {string} fieldId - Field ID
         * @param {Array} images - Array of image objects
         */
        setValue: function(fieldId, images) {
            if (window.updateImageList) {
                window.updateImageList(fieldId, images);
            }
        },
        
        handlers: {
            // Handlers are attached to window for backwards compatibility
        }
    });

    // ===== File Upload Handlers (Backwards Compatible) =====
    // These functions are called from the server-rendered template
    
    /**
     * Handle file drop event
     * @param {Event} event - Drop event
     * @param {string} fieldId - Field ID
     */
    window.handleFileDrop = function(event, fieldId) {
        event.preventDefault();
        const files = event.dataTransfer.files;
        if (files.length === 0) return;
        // Route to single-file handler only for non-multiple string file-upload widgets
        const configEl = getConfigSourceElement(fieldId);
        const isMultiple = configEl && configEl.dataset.multiple === 'true';
        if (!isMultiple && configEl && configEl.dataset.uploadEndpoint && configEl.dataset.uploadEndpoint.trim() !== '') {
            window.handleSingleFileUpload(fieldId, files[0]);
        } else {
            window.handleFiles(fieldId, Array.from(files));
        }
    };

    /**
     * Handle file select event
     * @param {Event} event - Change event
     * @param {string} fieldId - Field ID
     */
    window.handleFileSelect = function(event, fieldId) {
        const files = event.target.files;
        if (files.length > 0) {
            window.handleFiles(fieldId, Array.from(files));
        }
    };

    /**
     * Handle single-file select for string file-upload widgets (e.g. credentials.json)
     * @param {Event} event - Change event
     * @param {string} fieldId - Field ID
     */
    window.handleSingleFileSelect = function(event, fieldId) {
        const files = event.target.files;
        if (files.length > 0) {
            window.handleSingleFileUpload(fieldId, files[0]);
        }
    };

    /**
     * Upload a single file for string file-upload widgets
     * Reads upload config from data attributes on the file input element.
     * @param {string} fieldId - Field ID
     * @param {File} file - File to upload
     */
    /**
     * Resolve the config source element for a field, checking file input first
     * then falling back to the drop zone wrapper (which survives re-renders).
     * @param {string} fieldId - Field ID
     * @returns {HTMLElement|null} Element with data attributes, or null
     */
    function getConfigSourceElement(fieldId) {
        const fileInput = document.getElementById(`${fieldId}_file_input`);
        if (fileInput && (fileInput.dataset.pluginId || fileInput.dataset.uploadEndpoint)) {
            return fileInput;
        }
        const dropZone = document.getElementById(`${fieldId}_drop_zone`);
        if (dropZone && (dropZone.dataset.pluginId || dropZone.dataset.uploadEndpoint)) {
            return dropZone;
        }
        return null;
    }

    window.handleSingleFileUpload = async function(fieldId, file) {
        // Read config from file input or drop zone fallback (survives re-renders)
        const configEl = getConfigSourceElement(fieldId);
        if (!configEl) return;

        const uploadEndpoint = configEl.dataset.uploadEndpoint;
        const targetFilename = configEl.dataset.targetFilename || 'file.json';
        const maxSizeMB = parseFloat(configEl.dataset.maxSizeMb || '1');
        const allowedExtensions = (configEl.dataset.allowedExtensions || '.json')
            .split(',').map(e => e.trim().toLowerCase());

        const statusDiv = document.getElementById(`${fieldId}_upload_status`);

        // Guard: endpoint must be configured
        if (!uploadEndpoint) {
            window.showNotification('No upload endpoint configured for this field', 'error');
            return;
        }

        // Validate extension
        const fileExt = '.' + file.name.split('.').pop().toLowerCase();
        if (!allowedExtensions.includes(fileExt)) {
            window.showNotification(`File must be one of: ${allowedExtensions.join(', ')}`, 'error');
            return;
        }

        // Validate size
        if (file.size > maxSizeMB * 1024 * 1024) {
            window.showNotification(`File exceeds ${maxSizeMB}MB limit`, 'error');
            return;
        }

        if (statusDiv) {
            statusDiv.className = 'mt-2 text-xs text-gray-500';
            statusDiv.textContent = '';
            const spinner = document.createElement('i');
            spinner.className = 'fas fa-spinner fa-spin mr-1';
            statusDiv.appendChild(spinner);
            statusDiv.appendChild(document.createTextNode('Uploading...'));
        }

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(uploadEndpoint, {
                method: 'POST',
                body: formData
            });
            if (!response.ok) {
                const body = await response.text();
                throw new Error(`Server error ${response.status}: ${body}`);
            }
            const data = await response.json();

            if (data.status === 'success') {
                if (statusDiv) {
                    statusDiv.className = 'mt-2 text-xs text-green-600';
                    statusDiv.textContent = '';
                    const icon = document.createElement('i');
                    icon.className = 'fas fa-check-circle mr-1';
                    statusDiv.appendChild(icon);
                    statusDiv.appendChild(document.createTextNode(`Uploaded: ${targetFilename}`));
                }
                // Update hidden input with the target filename
                const hiddenInput = document.getElementById(fieldId);
                if (hiddenInput) hiddenInput.value = targetFilename;
                window.showNotification(`${targetFilename} uploaded successfully`, 'success');
            } else {
                if (statusDiv) {
                    statusDiv.className = 'mt-2 text-xs text-red-600';
                    statusDiv.textContent = '';
                    const icon = document.createElement('i');
                    icon.className = 'fas fa-exclamation-circle mr-1';
                    statusDiv.appendChild(icon);
                    statusDiv.appendChild(document.createTextNode(`Upload failed: ${data.message}`));
                }
                window.showNotification(`Upload failed: ${data.message}`, 'error');
            }
        } catch (error) {
            if (statusDiv) {
                statusDiv.className = 'mt-2 text-xs text-red-600';
                statusDiv.textContent = '';
                const icon = document.createElement('i');
                icon.className = 'fas fa-exclamation-circle mr-1';
                statusDiv.appendChild(icon);
                statusDiv.appendChild(document.createTextNode(`Upload error: ${error.message}`));
            }
            window.showNotification(`Upload error: ${error.message}`, 'error');
        } finally {
            const fileInput = document.getElementById(`${fieldId}_file_input`);
            if (fileInput) fileInput.value = '';
        }
    };

    /**
     * Handle multiple files upload
     * @param {string} fieldId - Field ID
     * @param {Array<File>} files - Files to upload
     */
    window.handleFiles = async function(fieldId, files) {
        const uploadConfig = window.getUploadConfig ? window.getUploadConfig(fieldId) : {};
        const pluginId = uploadConfig.plugin_id || window.currentPluginConfig?.pluginId || 'static-image';
        const maxFiles = uploadConfig.max_files || 10;
        const maxSizeMB = uploadConfig.max_size_mb || 5;
        const fileType = uploadConfig.file_type || 'image';
        const customUploadEndpoint = uploadConfig.endpoint || '/api/v3/plugins/assets/upload';
        
        // Get allowed types from config, with fallback
        const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp', 'image/gif'];
        
        // Get current files list
        const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
        
        // Validate file types and sizes first, build validFiles
        const validFiles = [];
        for (const file of files) {
            if (file.size > maxSizeMB * 1024 * 1024) {
                window.showNotification(`File ${file.name} exceeds ${maxSizeMB}MB limit`, 'error');
                continue;
            }
            
            if (fileType === 'json') {
                // Validate JSON files
                if (!file.name.toLowerCase().endsWith('.json')) {
                    window.showNotification(`File ${file.name} must be a JSON file (.json)`, 'error');
                    continue;
                }
            } else {
                // Validate image files using allowedTypes from config
                if (!allowedTypes.includes(file.type)) {
                    window.showNotification(`File ${file.name} is not a valid image type`, 'error');
                    continue;
                }
            }
            
            validFiles.push(file);
        }
        
        // Check max files AFTER building validFiles
        if (currentFiles.length + validFiles.length > maxFiles) {
            window.showNotification(`Maximum ${maxFiles} files allowed. You have ${currentFiles.length} and tried to add ${validFiles.length}.`, 'error');
            return;
        }
        
        if (validFiles.length === 0) {
            return;
        }
        
        // Show upload progress
        if (window.showUploadProgress) {
            window.showUploadProgress(fieldId, validFiles.length);
        }
        
        // Upload files
        const formData = new FormData();
        if (fileType !== 'json') {
            formData.append('plugin_id', pluginId);
        }
        validFiles.forEach(file => { formData.append('files', file); });
        
        try {
            const response = await fetch(customUploadEndpoint, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const body = await response.text();
                throw new Error(`Server error ${response.status}: ${body}`);
            }

            const data = await response.json();

            if (data.status === 'success') {
                // Add uploaded files to current list
                const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
                const newFiles = [...currentFiles, ...(data.uploaded_files || data.data?.files || [])];
                if (window.updateImageList) {
                    window.updateImageList(fieldId, newFiles);
                }
                
                window.showNotification(`Successfully uploaded ${data.uploaded_files?.length || data.data?.files?.length || 0} ${fileType === 'json' ? 'file(s)' : 'image(s)'}`, 'success');
            } else {
                window.showNotification(`Upload failed: ${data.message}`, 'error');
            }
        } catch (error) {
            console.error('Upload error:', error);
            window.showNotification(`Upload error: ${error.message}`, 'error');
        } finally {
            if (window.hideUploadProgress) {
                window.hideUploadProgress(fieldId);
            }
            // Clear file input
            const fileInput = document.getElementById(`${fieldId}_file_input`);
            if (fileInput) {
                fileInput.value = '';
            }
        }
    };

    /**
     * Delete uploaded image
     * @param {string} fieldId - Field ID
     * @param {string} imageId - Image ID
     * @param {string} pluginId - Plugin ID
     */
    window.deleteUploadedImage = async function(fieldId, imageId, pluginId) {
        return window.deleteUploadedFile(fieldId, imageId, pluginId, 'image', null);
    };

    /**
     * Delete uploaded file (generic)
     * @param {string} fieldId - Field ID
     * @param {string} fileId - File ID
     * @param {string} pluginId - Plugin ID
     * @param {string} fileType - File type ('image' or 'json')
     * @param {string|null} customDeleteEndpoint - Custom delete endpoint
     */
    window.deleteUploadedFile = async function(fieldId, fileId, pluginId, fileType, customDeleteEndpoint) {
        const fileTypeLabel = fileType === 'json' ? 'file' : 'image';
        if (!confirm(`Are you sure you want to delete this ${fileTypeLabel}?`)) {
            return;
        }
        
        try {
            const deleteEndpoint = customDeleteEndpoint || (fileType === 'json' ? '/api/v3/plugins/of-the-day/json/delete' : '/api/v3/plugins/assets/delete');
            const requestBody = fileType === 'json' 
                ? { file_id: fileId }
                : { plugin_id: pluginId, image_id: fileId };
            
            const response = await fetch(deleteEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                const body = await response.text();
                throw new Error(`Server error ${response.status}: ${body}`);
            }

            const data = await response.json();

            if (data.status === 'success') {
                // Remove from current list - normalize types for comparison
                const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
                const fileIdStr = String(fileId);
                const newFiles = currentFiles.filter(file => {
                    const fileIdValue = String(file.id || file.category_name || '');
                    return fileIdValue !== fileIdStr;
                });
                if (window.updateImageList) {
                    window.updateImageList(fieldId, newFiles);
                }
                
                window.showNotification(`${fileType === 'json' ? 'File' : 'Image'} deleted successfully`, 'success');
            } else {
                window.showNotification(`Delete failed: ${data.message}`, 'error');
            }
        } catch (error) {
            console.error('Delete error:', error);
            window.showNotification(`Delete error: ${error.message}`, 'error');
        }
    };

    /**
     * Get upload configuration for a file upload field.
     * Priority: 1) data attributes on the file input element (server-rendered),
     *           2) schema lookup via window.currentPluginConfig (client-rendered).
     * @param {string} fieldId - Field ID
     * @returns {Object} Upload configuration
     */
    window.getUploadConfig = function(fieldId) {
        // Strategy 1: Read from data attributes on the file input element or
        // the drop zone wrapper (which survives progress-helper re-renders).
        // Accept any upload-related data attribute — not just pluginId.
        const configSource = getConfigSourceElement(fieldId);
        if (configSource) {
            const ds = configSource.dataset;
            const config = {};
            if (ds.pluginId) config.plugin_id = ds.pluginId;
            if (ds.uploadEndpoint) config.endpoint = ds.uploadEndpoint;
            if (ds.fileType) config.file_type = ds.fileType;
            if (ds.maxFiles) config.max_files = parseInt(ds.maxFiles, 10);
            if (ds.maxSizeMb) config.max_size_mb = parseFloat(ds.maxSizeMb);
            if (ds.allowedTypes) {
                config.allowed_types = ds.allowedTypes.split(',').map(t => t.trim());
            }
            return config;
        }

        // Strategy 2: Extract config from schema (client-side rendered forms)
        const schema = window.currentPluginConfig?.schema;
        if (!schema || !schema.properties) return {};

        // Find the property that matches this fieldId
        // FieldId is like "image_config_images" for "image_config.images" (client-side)
        // or "static-image-images" for plugin "static-image", field "images" (server-side)
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

        // Try to find nested images array (legacy fallback)
        if (schema.properties && schema.properties.image_config &&
            schema.properties.image_config.properties &&
            schema.properties.image_config.properties.images) {
            const imagesProp = schema.properties.image_config.properties.images;
            if (imagesProp['x-widget'] === 'file-upload') {
                return imagesProp['x-upload-config'] || {};
            }
        }

        return {};
    };

    /**
     * Get current images from hidden input
     * @param {string} fieldId - Field ID
     * @returns {Array} Array of image objects
     */
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
    };

    // DOM id suffix for an image's card and schedule editor. Matches the
    // template's img_id|replace('.', '_')|replace('-', '_') in
    // plugin_config.html, so server-rendered cards (UUID ids contain '-') and
    // cards rendered here are found by the same lookup.
    function imageDomId(imageId) {
        return String(imageId).replace(/[^a-zA-Z0-9_]/g, '_');
    }

    /**
     * Replace the image list: writes the hidden input the form saves and
     * re-renders the cards. A schedule editor that was open stays open,
     * rebuilt from the new data.
     * @param {string} fieldId - Field ID
     * @param {Array} images - Array of image objects
     */
    window.updateImageList = function(fieldId, images) {
        const hiddenInput = document.getElementById(`${fieldId}_images_data`);
        if (hiddenInput) {
            hiddenInput.value = JSON.stringify(images);
        }

        const imageList = document.getElementById(`${fieldId}_image_list`);
        if (!imageList) return;

        const uploadConfig = window.getUploadConfig(fieldId);
        const pluginId = uploadConfig.plugin_id || window.currentPluginConfig?.pluginId || 'static-image';

        const openEditor = imageList.querySelector('[id^="schedule_"]:not(.hidden)');
        const openScheduleId = openEditor ? openEditor.id.slice('schedule_'.length) : null;

        imageList.innerHTML = '';

        images.forEach((img, idx) => {
            const imgId = img.id || idx;
            const domId = imageDomId(imgId);
            const label = img.original_filename || img.filename || '';

            const container = document.createElement('div');
            container.id = `img_${domId}`;
            container.className = 'bg-gray-50 p-3 rounded-lg border border-gray-200';

            const mainDiv = document.createElement('div');
            mainDiv.className = 'flex items-center justify-between mb-2';

            const leftSection = document.createElement('div');
            leftSection.className = 'flex items-center space-x-3 flex-1';

            const imgEl = document.createElement('img');
            // A stored path names a file under the project root. Encoding each
            // segment keeps it a same-origin path whatever characters it holds.
            imgEl.src = '/' + String(img.path || '').replace(/^\/+/, '')
                .split('/').map(encodeURIComponent).join('/');
            imgEl.alt = String(img.filename || '');
            imgEl.loading = 'lazy';
            imgEl.decoding = 'async';
            imgEl.className = 'w-16 h-16 object-cover rounded';
            imgEl.addEventListener('error', function() {
                this.style.display = 'none';
                if (this.nextElementSibling) {
                    this.nextElementSibling.style.display = 'block';
                }
            });

            // Shown in place of a thumbnail that fails to load
            const placeholderDiv = document.createElement('div');
            placeholderDiv.style.display = 'none';
            placeholderDiv.className = 'w-16 h-16 bg-gray-200 rounded flex items-center justify-center';
            const placeholderIcon = document.createElement('i');
            placeholderIcon.className = 'fas fa-image text-gray-400';
            placeholderDiv.appendChild(placeholderIcon);

            const infoDiv = document.createElement('div');
            infoDiv.className = 'flex-1 min-w-0';

            const filenameP = document.createElement('p');
            filenameP.className = 'text-sm font-medium text-gray-900 truncate';
            filenameP.textContent = label || 'Image';

            const sizeDateP = document.createElement('p');
            sizeDateP.className = 'text-xs text-gray-500';
            sizeDateP.textContent = `${window.formatFileSize(img.size || 0)} • ${window.formatDate(img.uploaded_at)}`;

            const scheduleP = document.createElement('p');
            scheduleP.className = 'text-xs text-blue-600 mt-1 image-schedule-summary';
            renderScheduleSummary(scheduleP, img.schedule);

            infoDiv.appendChild(filenameP);
            infoDiv.appendChild(sizeDateP);
            infoDiv.appendChild(scheduleP);

            leftSection.appendChild(imgEl);
            leftSection.appendChild(placeholderDiv);
            leftSection.appendChild(infoDiv);

            const rightSection = document.createElement('div');
            rightSection.className = 'flex items-center space-x-2 ml-4';

            const scheduleBtn = document.createElement('button');
            scheduleBtn.type = 'button';
            scheduleBtn.className = 'text-blue-600 hover:text-blue-800 p-2';
            scheduleBtn.title = 'Schedule this image';
            scheduleBtn.setAttribute('aria-label', `Schedule image ${label}`);
            scheduleBtn.dataset.fieldId = fieldId;
            scheduleBtn.dataset.imageId = String(imgId);
            scheduleBtn.dataset.imageIdx = String(idx);
            scheduleBtn.addEventListener('click', function() {
                window.openImageSchedule(this.dataset.fieldId, this.dataset.imageId, parseInt(this.dataset.imageIdx, 10));
            });
            scheduleBtn.appendChild(iconEl('fas fa-calendar-alt'));

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'text-red-600 hover:text-red-800 p-2';
            deleteBtn.title = 'Delete image';
            deleteBtn.setAttribute('aria-label', `Delete image ${label}`);
            deleteBtn.dataset.fieldId = fieldId;
            deleteBtn.dataset.imageId = String(imgId);
            deleteBtn.dataset.pluginId = pluginId;
            deleteBtn.addEventListener('click', function() {
                window.deleteUploadedImage(this.dataset.fieldId, this.dataset.imageId, this.dataset.pluginId);
            });
            deleteBtn.appendChild(iconEl('fas fa-trash'));

            rightSection.appendChild(scheduleBtn);
            rightSection.appendChild(deleteBtn);

            mainDiv.appendChild(leftSection);
            mainDiv.appendChild(rightSection);

            const scheduleContainer = document.createElement('div');
            scheduleContainer.id = `schedule_${domId}`;
            scheduleContainer.className = 'hidden mt-3 pt-3 border-t border-gray-300';

            container.appendChild(mainDiv);
            container.appendChild(scheduleContainer);
            imageList.appendChild(container);

            if (openScheduleId === domId) {
                renderScheduleEditor(scheduleContainer, fieldId, imgId, idx, img.schedule);
                scheduleContainer.classList.remove('hidden');
            }
        });
    };

    // Decorative icon, hidden from screen readers (the button has a label)
    function iconEl(className) {
        const i = document.createElement('i');
        i.className = className;
        i.setAttribute('aria-hidden', 'true');
        return i;
    }

    function renderScheduleSummary(el, schedule) {
        el.textContent = '';
        const clock = document.createElement('i');
        clock.className = 'fas fa-clock mr-1';
        el.appendChild(clock);
        el.appendChild(document.createTextNode(window.getScheduleSummary(schedule || {})));
    }

    // Saves a schedule edit: writes the hidden input and refreshes that
    // card's summary line in place. The list is not re-rendered, so the open
    // editor keeps its state and the control being edited keeps focus.
    function commitScheduleEdit(fieldId, images, imageId) {
        const hiddenInput = document.getElementById(`${fieldId}_images_data`);
        if (hiddenInput) {
            hiddenInput.value = JSON.stringify(images);
        }
        const card = document.getElementById(`img_${imageDomId(imageId)}`);
        const image = images.find((img, idx) => String(img.id || idx) === String(imageId));
        const summary = card && card.querySelector('.image-schedule-summary');
        if (summary && image) {
            renderScheduleSummary(summary, image.schedule);
        }
    }

    /**
     * Show upload progress
     * @param {string} fieldId - Field ID
     * @param {number} totalFiles - Total number of files
     */
    window.showUploadProgress = function(fieldId, totalFiles) {
        const dropZone = document.getElementById(`${fieldId}_drop_zone`);
        if (dropZone) {
            dropZone.innerHTML = `
                <i class="fas fa-spinner fa-spin text-3xl text-blue-500 mb-2"></i>
                <p class="text-sm text-gray-600">Uploading ${totalFiles} file(s)...</p>
            `;
            dropZone.style.pointerEvents = 'none';
        }
    };

    /**
     * Hide upload progress and restore drop zone
     * @param {string} fieldId - Field ID
     */
    window.hideUploadProgress = function(fieldId) {
        const uploadConfig = window.getUploadConfig(fieldId);
        const maxFiles = uploadConfig.max_files || 10;
        const maxSizeMB = uploadConfig.max_size_mb || 5;
        const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/bmp', 'image/gif'];
        
        // Generate user-friendly extension list from allowedTypes
        const extensionMap = {
            'image/png': 'PNG',
            'image/jpeg': 'JPG',
            'image/jpg': 'JPG',
            'image/bmp': 'BMP',
            'image/gif': 'GIF',
            'image/webp': 'WEBP'
        };
        const extensions = allowedTypes
            .map(type => extensionMap[type] || type.split('/')[1]?.toUpperCase() || type)
            .filter((ext, idx, arr) => arr.indexOf(ext) === idx) // Remove duplicates
            .join(', ');
        const extensionText = extensions || 'PNG, JPG, GIF, BMP';
        
        const dropZone = document.getElementById(`${fieldId}_drop_zone`);
        if (dropZone) {
            dropZone.innerHTML = `
                <i class="fas fa-cloud-upload-alt text-3xl text-gray-400 mb-2"></i>
                <p class="text-sm text-gray-600">Drag and drop images here or click to browse</p>
                <p class="text-xs text-gray-500 mt-1">Max ${maxFiles} files, ${maxSizeMB}MB each (${extensionText})</p>
            `;
            dropZone.style.pointerEvents = 'auto';
        }
    };

    /**
     * Format file size
     * @param {number} bytes - File size in bytes
     * @returns {string} Formatted file size
     */
    window.formatFileSize = function(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    };

    /**
     * Format date string
     * @param {string} dateString - Date string
     * @returns {string} Formatted date
     */
    window.formatDate = function(dateString) {
        if (!dateString) return 'Unknown date';
        try {
            const date = new Date(dateString);
            return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch {
            return dateString;
        }
    };

    /**
     * Get schedule summary text
     * @param {Object} schedule - Schedule object
     * @returns {string} Schedule summary
     */
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
    };

    /**
     * Show or hide an image's schedule editor.
     * @param {string} fieldId - Field ID
     * @param {string|number} imageId - Image ID (the index when the image has none)
     * @param {number} imageIdx - Image index
     */
    window.openImageSchedule = function(fieldId, imageId, imageIdx) {
        const idx = Number(imageIdx);
        const image = Number.isInteger(idx) && idx >= 0 ? window.getCurrentImages(fieldId).at(idx) : undefined;
        if (!image) return;

        const scheduleContainer = document.getElementById(`schedule_${imageDomId(imageId || imageIdx)}`);
        if (!scheduleContainer) return;

        if (!scheduleContainer.classList.contains('hidden')) {
            scheduleContainer.classList.add('hidden');
            return;
        }
        renderScheduleEditor(scheduleContainer, fieldId, imageId || imageIdx, imageIdx, image.schedule);
        scheduleContainer.classList.remove('hidden');
    };

    const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

    // Builds a DOM element. Attribute values and text children go through the
    // DOM APIs, so nothing passed in is ever parsed as HTML. `true` sets an
    // empty (boolean) attribute; `false`, null and undefined leave it off.
    function h(tag, attrs, children) {
        const node = document.createElement(tag);
        for (const [name, value] of Object.entries(attrs || {})) {
            if (value === false || value == null) continue;
            node.setAttribute(name, value === true ? '' : String(value));
        }
        for (const child of children || []) node.append(child);
        return node;
    }

    function displayStyle(visible, shown) {
        return `display: ${visible ? shown : 'none'};`;
    }

    function scheduleModeOption(schedule, value, label) {
        return h('option', { value: value, selected: schedule.mode === value }, [label]);
    }

    function scheduleRangeTime(which, label, value, domId, ids) {
        return h('div', {}, [
            h('label', { for: `schedule_${which}_${domId}`, class: 'block text-xs font-medium text-gray-700 mb-1' }, [label]),
            h('input', {
                type: 'time', id: `schedule_${which}_${domId}`, 'data-schedule-control': 'time', ...ids,
                value: value, class: 'block w-full px-2 py-1 text-sm border border-gray-300 rounded-md',
            }),
        ]);
    }

    function scheduleDayTime(day, which, value, enabled, domId, dayIds) {
        return h('input', {
            type: 'time', id: `day_${day}_${which}_${domId}`, 'aria-label': `${day} ${which} time`, ...dayIds,
            value: value, class: 'text-xs px-2 py-1 border border-gray-300 rounded', disabled: !enabled,
        });
    }

    function scheduleDayRow(schedule, day, domId, ids) {
        const dayConfig = (schedule.days && schedule.days[day]) || { enabled: true, start_time: '08:00', end_time: '18:00' };
        const dayIds = { 'data-schedule-control': 'day', 'data-day': day, ...ids };
        return h('div', { class: 'bg-white rounded p-2 border border-gray-200' }, [
            h('div', { class: 'flex items-center justify-between mb-2' }, [
                h('label', { class: 'flex items-center' }, [
                    h('input', {
                        type: 'checkbox', id: `day_${day}_${domId}`, ...dayIds, checked: !!dayConfig.enabled,
                        class: 'h-3 w-3 text-blue-600 focus:ring-blue-500 border-gray-300 rounded',
                    }),
                    h('span', { class: 'ml-2 text-xs font-medium text-gray-700 capitalize' }, [day]),
                ]),
            ]),
            h('div', { class: 'grid grid-cols-2 gap-2 ml-5', id: `day_times_${day}_${domId}`, style: displayStyle(dayConfig.enabled, 'grid') }, [
                scheduleDayTime(day, 'start', dayConfig.start_time || '08:00', dayConfig.enabled, domId, dayIds),
                scheduleDayTime(day, 'end', dayConfig.end_time || '18:00', dayConfig.enabled, domId, dayIds),
            ]),
        ]);
    }

    // Builds the schedule editor. Controls carry data-schedule-control and
    // their ids as data attributes; one delegated change listener (below)
    // routes them, so the editor needs no per-element listeners.
    function renderScheduleEditor(container, fieldId, imageId, imageIdx, savedSchedule) {
        const schedule = savedSchedule || { enabled: false, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
        const domId = imageDomId(imageId);
        const ids = { 'data-field-id': fieldId, 'data-image-id': imageId, 'data-image-idx': Number(imageIdx) };
        const dayRows = [];
        for (const day of DAYS) dayRows.push(scheduleDayRow(schedule, day, domId, ids));

        container.replaceChildren(h('div', { class: 'bg-white rounded-lg border border-blue-200 p-4' }, [
            h('h4', { class: 'text-sm font-semibold text-gray-900 mb-3' }, [
                h('i', { class: 'fas fa-clock mr-2' }), 'Schedule Settings',
            ]),
            h('div', { class: 'mb-4' }, [
                h('label', { class: 'flex items-center' }, [
                    h('input', {
                        type: 'checkbox', id: `schedule_enabled_${domId}`, 'data-schedule-control': 'enabled', ...ids,
                        checked: !!schedule.enabled, class: 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded',
                    }),
                    h('span', { class: 'ml-2 text-sm font-medium text-gray-700' }, ['Enable schedule for this image']),
                ]),
                h('p', { class: 'ml-6 text-xs text-gray-500 mt-1' }, ['When enabled, this image will only display during scheduled times']),
            ]),
            h('div', { id: `schedule_options_${domId}`, class: 'space-y-4', style: displayStyle(schedule.enabled, 'block') }, [
                h('div', {}, [
                    h('label', { for: `schedule_mode_${domId}`, class: 'block text-sm font-medium text-gray-700 mb-2' }, ['Schedule Type']),
                    h('select', {
                        id: `schedule_mode_${domId}`, 'data-schedule-control': 'mode', ...ids,
                        class: 'block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm',
                    }, [
                        scheduleModeOption(schedule, 'always', 'Always Show (No Schedule)'),
                        scheduleModeOption(schedule, 'time_range', 'Same Time Every Day'),
                        scheduleModeOption(schedule, 'per_day', 'Different Times Per Day'),
                    ]),
                ]),
                h('div', { id: `time_range_${domId}`, class: 'grid grid-cols-2 gap-4', style: displayStyle(schedule.mode === 'time_range', 'grid') }, [
                    scheduleRangeTime('start', 'Start Time', schedule.start_time || '08:00', domId, ids),
                    scheduleRangeTime('end', 'End Time', schedule.end_time || '18:00', domId, ids),
                ]),
                h('div', { id: `per_day_${domId}`, style: displayStyle(schedule.mode === 'per_day', 'block') }, [
                    h('label', { class: 'block text-xs font-medium text-gray-700 mb-2' }, ['Day-Specific Times']),
                    h('div', { class: 'bg-gray-50 rounded p-3 space-y-2 max-h-64 overflow-y-auto' }, dayRows),
                ]),
            ]),
        ]));
    }

    document.addEventListener('change', function(event) {
        const el = event.target && event.target.closest && event.target.closest('[data-schedule-control]');
        if (!el) return;
        const { fieldId, imageId, day } = el.dataset;
        const imageIdx = parseInt(el.dataset.imageIdx, 10);
        switch (el.dataset.scheduleControl) {
            case 'enabled': window.toggleImageScheduleEnabled(fieldId, imageId, imageIdx); break;
            case 'mode': window.updateImageScheduleMode(fieldId, imageId, imageIdx); break;
            case 'time': window.updateImageScheduleTime(fieldId, imageId, imageIdx); break;
            case 'day': window.updateImageScheduleDay(fieldId, imageId, imageIdx, day); break;
        }
    });

    window.toggleImageScheduleEnabled = function(fieldId, imageId, imageIdx) {
        const currentImages = window.getCurrentImages(fieldId);
        const image = currentImages[imageIdx];
        if (!image) return;

        const domId = imageDomId(imageId);
        const checkbox = document.getElementById(`schedule_enabled_${domId}`);
        const enabled = checkbox ? checkbox.checked : false;

        if (!image.schedule) {
            image.schedule = { enabled: false, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
        }
        image.schedule.enabled = enabled;

        const optionsDiv = document.getElementById(`schedule_options_${domId}`);
        if (optionsDiv) {
            optionsDiv.style.display = enabled ? 'block' : 'none';
        }

        commitScheduleEdit(fieldId, currentImages, imageId);
    };

    window.updateImageScheduleMode = function(fieldId, imageId, imageIdx) {
        const currentImages = window.getCurrentImages(fieldId);
        const image = currentImages[imageIdx];
        if (!image) return;

        const domId = imageDomId(imageId);
        if (!image.schedule) {
            image.schedule = { enabled: true, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
        }

        const modeSelect = document.getElementById(`schedule_mode_${domId}`);
        const mode = modeSelect ? modeSelect.value : 'always';
        image.schedule.mode = mode;

        const timeRangeDiv = document.getElementById(`time_range_${domId}`);
        const perDayDiv = document.getElementById(`per_day_${domId}`);
        if (timeRangeDiv) timeRangeDiv.style.display = mode === 'time_range' ? 'grid' : 'none';
        if (perDayDiv) perDayDiv.style.display = mode === 'per_day' ? 'block' : 'none';

        commitScheduleEdit(fieldId, currentImages, imageId);
    };

    window.updateImageScheduleTime = function(fieldId, imageId, imageIdx) {
        const currentImages = window.getCurrentImages(fieldId);
        const image = currentImages[imageIdx];
        if (!image) return;

        const domId = imageDomId(imageId);
        if (!image.schedule) {
            image.schedule = { enabled: true, mode: 'time_range', start_time: '08:00', end_time: '18:00' };
        }

        const startInput = document.getElementById(`schedule_start_${domId}`);
        const endInput = document.getElementById(`schedule_end_${domId}`);
        if (startInput) image.schedule.start_time = startInput.value || '08:00';
        if (endInput) image.schedule.end_time = endInput.value || '18:00';

        commitScheduleEdit(fieldId, currentImages, imageId);
    };

    window.updateImageScheduleDay = function(fieldId, imageId, imageIdx, day) {
        const currentImages = window.getCurrentImages(fieldId);
        const image = currentImages[imageIdx];
        if (!image) return;

        const domId = imageDomId(imageId);
        if (!image.schedule) {
            image.schedule = { enabled: true, mode: 'per_day', days: {} };
        }
        if (!image.schedule.days) {
            image.schedule.days = {};
        }

        const checkbox = document.getElementById(`day_${day}_${domId}`);
        const startInput = document.getElementById(`day_${day}_start_${domId}`);
        const endInput = document.getElementById(`day_${day}_end_${domId}`);
        const enabled = checkbox ? checkbox.checked : true;

        if (!image.schedule.days[day]) {
            image.schedule.days[day] = { enabled: true, start_time: '08:00', end_time: '18:00' };
        }
        image.schedule.days[day].enabled = enabled;
        if (startInput) image.schedule.days[day].start_time = startInput.value || '08:00';
        if (endInput) image.schedule.days[day].end_time = endInput.value || '18:00';

        const dayTimesDiv = document.getElementById(`day_times_${day}_${domId}`);
        if (dayTimesDiv) {
            dayTimesDiv.style.display = enabled ? 'grid' : 'none';
        }
        if (startInput) startInput.disabled = !enabled;
        if (endInput) endInput.disabled = !enabled;

        commitScheduleEdit(fieldId, currentImages, imageId);
    };
})();
