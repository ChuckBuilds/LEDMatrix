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
         * Render the file upload widget
         * Note: This widget is currently server-side rendered via Jinja2 template.
         * This registration ensures the handlers are available globally.
         * Future enhancement: Full client-side rendering support.
         */
        render: function(container, config, value, options) {
            // For now, widgets are server-side rendered
            // This function is a placeholder for future client-side rendering
            console.log('[FileUploadWidget] Render called (server-side rendered)');
        },
        
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
        if (files.length > 0) {
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
        
        // Get current files list
        const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
        if (currentFiles.length + files.length > maxFiles) {
            const notifyFn = window.showNotification || console.error;
            notifyFn(`Maximum ${maxFiles} files allowed. You have ${currentFiles.length} and tried to add ${files.length}.`, 'error');
            return;
        }
        
        // Validate file types and sizes
        const validFiles = [];
        for (const file of files) {
            if (file.size > maxSizeMB * 1024 * 1024) {
                const notifyFn = window.showNotification || console.error;
                notifyFn(`File ${file.name} exceeds ${maxSizeMB}MB limit`, 'error');
                continue;
            }
            
            if (fileType === 'json') {
                // Validate JSON files
                if (!file.name.toLowerCase().endsWith('.json')) {
                    const notifyFn = window.showNotification || console.error;
                    notifyFn(`File ${file.name} must be a JSON file (.json)`, 'error');
                    continue;
                }
            } else {
                // Validate image files
                const allowedTypes = ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp', 'image/gif'];
                if (!allowedTypes.includes(file.type)) {
                    const notifyFn = window.showNotification || console.error;
                    notifyFn(`File ${file.name} is not a valid image type`, 'error');
                    continue;
                }
            }
            
            validFiles.push(file);
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
        validFiles.forEach(file => formData.append('files', file));
        
        try {
            const response = await fetch(customUploadEndpoint, {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                // Add uploaded files to current list
                const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
                const newFiles = [...currentFiles, ...(data.uploaded_files || data.data?.files || [])];
                if (window.updateImageList) {
                    window.updateImageList(fieldId, newFiles);
                }
                
                const notifyFn = window.showNotification || console.log;
                notifyFn(`Successfully uploaded ${data.uploaded_files?.length || data.data?.files?.length || 0} ${fileType === 'json' ? 'file(s)' : 'image(s)'}`, 'success');
            } else {
                const notifyFn = window.showNotification || console.error;
                notifyFn(`Upload failed: ${data.message}`, 'error');
            }
        } catch (error) {
            console.error('Upload error:', error);
            const notifyFn = window.showNotification || console.error;
            notifyFn(`Upload error: ${error.message}`, 'error');
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
            
            const data = await response.json();
            
            if (data.status === 'success') {
                // Remove from current list
                const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
                const newFiles = currentFiles.filter(file => (file.id || file.category_name) !== fileId);
                if (window.updateImageList) {
                    window.updateImageList(fieldId, newFiles);
                }
                
                const notifyFn = window.showNotification || console.log;
                notifyFn(`${fileType === 'json' ? 'File' : 'Image'} deleted successfully`, 'success');
            } else {
                const notifyFn = window.showNotification || console.error;
                notifyFn(`Delete failed: ${data.message}`, 'error');
            }
        } catch (error) {
            console.error('Delete error:', error);
            const notifyFn = window.showNotification || console.error;
            notifyFn(`Delete error: ${error.message}`, 'error');
        }
    };

    /**
     * Get upload configuration from schema
     * @param {string} fieldId - Field ID
     * @returns {Object} Upload configuration
     */
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

    /**
     * Update image list display and hidden input
     * @param {string} fieldId - Field ID
     * @param {Array} images - Array of image objects
     */
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
                
                // Escape HTML to prevent XSS
                const escapeHtml = (text) => {
                    const div = document.createElement('div');
                    div.textContent = text;
                    return div.innerHTML;
                };
                
                return `
                <div id="img_${(img.id || idx).toString().replace(/[^a-zA-Z0-9_-]/g, '_')}" class="bg-gray-50 p-3 rounded-lg border border-gray-200">
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center space-x-3 flex-1">
                            <img src="/${escapeHtml(img.path || '')}" 
                                 alt="${escapeHtml(img.filename || '')}" 
                                 class="w-16 h-16 object-cover rounded"
                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                            <div style="display:none;" class="w-16 h-16 bg-gray-200 rounded flex items-center justify-center">
                                <i class="fas fa-image text-gray-400"></i>
                            </div>
                            <div class="flex-1 min-w-0">
                                <p class="text-sm font-medium text-gray-900 truncate">${escapeHtml(img.original_filename || img.filename || 'Image')}</p>
                                <p class="text-xs text-gray-500">${window.formatFileSize ? window.formatFileSize(img.size || 0) : (Math.round((img.size || 0) / 1024) + ' KB')} • ${window.formatDate ? window.formatDate(img.uploaded_at) : (img.uploaded_at || '')}</p>
                                <p class="text-xs text-blue-600 mt-1">
                                    <i class="fas fa-clock mr-1"></i>${escapeHtml(scheduleSummary)}
                                </p>
                            </div>
                        </div>
                        <div class="flex items-center space-x-2 ml-4">
                            <button type="button" 
                                    onclick="window.openImageSchedule('${fieldId}', '${img.id || idx}', ${idx})"
                                    class="text-blue-600 hover:text-blue-800 p-2" 
                                    title="Schedule this image">
                                <i class="fas fa-calendar-alt"></i>
                            </button>
                            <button type="button" 
                                    onclick="window.deleteUploadedImage('${fieldId}', '${img.id || idx}', '${pluginId}')"
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
            }).join('');
        }
    };

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
        
        const dropZone = document.getElementById(`${fieldId}_drop_zone`);
        if (dropZone) {
            dropZone.innerHTML = `
                <i class="fas fa-cloud-upload-alt text-3xl text-gray-400 mb-2"></i>
                <p class="text-sm text-gray-600">Drag and drop images here or click to browse</p>
                <p class="text-xs text-gray-500 mt-1">Max ${maxFiles} files, ${maxSizeMB}MB each (PNG, JPG, GIF, BMP)</p>
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
        } catch (e) {
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
     * Open image schedule editor
     * @param {string} fieldId - Field ID
     * @param {string|number} imageId - Image ID
     * @param {number} imageIdx - Image index
     */
    window.openImageSchedule = function(fieldId, imageId, imageIdx) {
        const currentImages = window.getCurrentImages(fieldId);
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
        
        // Escape HTML helper
        const escapeHtml = (text) => {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        };
        
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
                                   value="${escapeHtml(schedule.start_time || '08:00')}"
                                   onchange="window.updateImageScheduleTime('${fieldId}', '${imageId}', ${imageIdx})"
                                   class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md">
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-700 mb-1">End Time</label>
                            <input type="time" 
                                   id="schedule_end_${imageId}"
                                   value="${escapeHtml(schedule.end_time || '18:00')}"
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
                                               value="${escapeHtml(dayConfig.start_time || '08:00')}"
                                               onchange="window.updateImageScheduleDay('${fieldId}', '${imageId}', ${imageIdx}, '${day}')"
                                               class="text-xs px-2 py-1 border border-gray-300 rounded"
                                               ${!dayConfig.enabled ? 'disabled' : ''}>
                                        <input type="time"
                                               id="day_${day}_end_${imageId}"
                                               value="${escapeHtml(dayConfig.end_time || '18:00')}"
                                               onchange="window.updateImageScheduleDay('${fieldId}', '${imageId}', ${imageIdx}, '${day}')"
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
    };

    /**
     * Toggle image schedule enabled state
     */
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
        
        if (window.updateImageList) {
            window.updateImageList(fieldId, currentImages);
        }
    };

    /**
     * Update image schedule mode
     */
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
        
        if (window.updateImageList) {
            window.updateImageList(fieldId, currentImages);
        }
    };

    /**
     * Update image schedule time
     */
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
        
        if (window.updateImageList) {
            window.updateImageList(fieldId, currentImages);
        }
    };

    /**
     * Update image schedule day
     */
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
        
        const dayTimesDiv = document.getElementById(`day_times_${day}_${imageId}`);
        if (dayTimesDiv) {
            dayTimesDiv.style.display = enabled ? 'grid' : 'none';
        }
        if (startInput) startInput.disabled = !enabled;
        if (endInput) endInput.disabled = !enabled;
        
        if (window.updateImageList) {
            window.updateImageList(fieldId, currentImages);
        }
    };

    console.log('[FileUploadWidget] File upload widget registered');
})();
