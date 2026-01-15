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
        
        // Get allowed types from config, with fallback
        const allowedTypes = uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp', 'image/gif'];
        
        // Get current files list
        const currentFiles = window.getCurrentImages ? window.getCurrentImages(fieldId) : [];
        
        // Validate file types and sizes first, build validFiles
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
                // Validate image files using allowedTypes from config
                if (!allowedTypes.includes(file.type)) {
                    const notifyFn = window.showNotification || console.error;
                    notifyFn(`File ${file.name} is not a valid image type`, 'error');
                    continue;
                }
            }
            
            validFiles.push(file);
        }
        
        // Check max files AFTER building validFiles
        if (currentFiles.length + validFiles.length > maxFiles) {
            const notifyFn = window.showNotification || console.error;
            notifyFn(`Maximum ${maxFiles} files allowed. You have ${currentFiles.length} and tried to add ${validFiles.length}.`, 'error');
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
     * Uses DOM creation to prevent XSS and preserves open schedule editors
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
        if (!imageList) return;
        
        const uploadConfig = window.getUploadConfig(fieldId);
        const pluginId = uploadConfig.plugin_id || window.currentPluginConfig?.pluginId || 'static-image';
        
        // Detect which schedule is currently open (if any)
        const openScheduleId = (() => {
            const existingItems = imageList.querySelectorAll('[id^="img_"]');
            for (const item of existingItems) {
                const scheduleDiv = item.querySelector('[id^="schedule_"]');
                if (scheduleDiv && !scheduleDiv.classList.contains('hidden')) {
                    // Extract the ID from schedule_<id>
                    const match = scheduleDiv.id.match(/^schedule_(.+)$/);
                    if (match) {
                        return match[1];
                    }
                }
            }
            return null;
        })();
        
        // Preserve open schedule content if it exists
        const preservedScheduleContent = openScheduleId ? (() => {
            const scheduleDiv = document.getElementById(`schedule_${openScheduleId}`);
            return scheduleDiv ? scheduleDiv.innerHTML : null;
        })() : null;
        
        // Clear and rebuild using DOM creation
        imageList.innerHTML = '';
        
        images.forEach((img, idx) => {
            const imgId = img.id || idx;
            const sanitizedId = String(imgId).replace(/[^a-zA-Z0-9_-]/g, '_');
            const imgSchedule = img.schedule || {};
            const hasSchedule = imgSchedule.enabled && imgSchedule.mode && imgSchedule.mode !== 'always';
            const scheduleSummary = hasSchedule ? (window.getScheduleSummary ? window.getScheduleSummary(imgSchedule) : 'Scheduled') : 'Always shown';
            
            // Create container div
            const container = document.createElement('div');
            container.id = `img_${sanitizedId}`;
            container.className = 'bg-gray-50 p-3 rounded-lg border border-gray-200';
            
            // Create main content div
            const mainDiv = document.createElement('div');
            mainDiv.className = 'flex items-center justify-between mb-2';
            
            // Create left section with image and info
            const leftSection = document.createElement('div');
            leftSection.className = 'flex items-center space-x-3 flex-1';
            
            // Create image element
            const imgEl = document.createElement('img');
            const imgPath = String(img.path || '').replace(/^\/+/, '');
            imgEl.src = '/' + imgPath;
            imgEl.alt = String(img.filename || '');
            imgEl.className = 'w-16 h-16 object-cover rounded';
            imgEl.addEventListener('error', function() {
                this.style.display = 'none';
                if (this.nextElementSibling) {
                    this.nextElementSibling.style.display = 'block';
                }
            });
            
            // Create placeholder div for broken images
            const placeholderDiv = document.createElement('div');
            placeholderDiv.style.display = 'none';
            placeholderDiv.className = 'w-16 h-16 bg-gray-200 rounded flex items-center justify-center';
            const placeholderIcon = document.createElement('i');
            placeholderIcon.className = 'fas fa-image text-gray-400';
            placeholderDiv.appendChild(placeholderIcon);
            
            // Create info div
            const infoDiv = document.createElement('div');
            infoDiv.className = 'flex-1 min-w-0';
            
            // Filename
            const filenameP = document.createElement('p');
            filenameP.className = 'text-sm font-medium text-gray-900 truncate';
            filenameP.textContent = img.original_filename || img.filename || 'Image';
            
            // Size and date
            const sizeDateP = document.createElement('p');
            sizeDateP.className = 'text-xs text-gray-500';
            const fileSize = window.formatFileSize ? window.formatFileSize(img.size || 0) : (Math.round((img.size || 0) / 1024) + ' KB');
            const uploadedDate = window.formatDate ? window.formatDate(img.uploaded_at) : (img.uploaded_at || '');
            sizeDateP.textContent = `${fileSize} • ${uploadedDate}`;
            
            // Schedule summary
            const scheduleP = document.createElement('p');
            scheduleP.className = 'text-xs text-blue-600 mt-1';
            const clockIcon = document.createElement('i');
            clockIcon.className = 'fas fa-clock mr-1';
            scheduleP.appendChild(clockIcon);
            scheduleP.appendChild(document.createTextNode(scheduleSummary));
            
            infoDiv.appendChild(filenameP);
            infoDiv.appendChild(sizeDateP);
            infoDiv.appendChild(scheduleP);
            
            leftSection.appendChild(imgEl);
            leftSection.appendChild(placeholderDiv);
            leftSection.appendChild(infoDiv);
            
            // Create right section with buttons
            const rightSection = document.createElement('div');
            rightSection.className = 'flex items-center space-x-2 ml-4';
            
            // Schedule button
            const scheduleBtn = document.createElement('button');
            scheduleBtn.type = 'button';
            scheduleBtn.className = 'text-blue-600 hover:text-blue-800 p-2';
            scheduleBtn.title = 'Schedule this image';
            scheduleBtn.dataset.fieldId = fieldId;
            scheduleBtn.dataset.imageId = String(imgId);
            scheduleBtn.dataset.imageIdx = String(idx);
            scheduleBtn.addEventListener('click', function() {
                window.openImageSchedule(this.dataset.fieldId, this.dataset.imageId, parseInt(this.dataset.imageIdx, 10));
            });
            const scheduleIcon = document.createElement('i');
            scheduleIcon.className = 'fas fa-calendar-alt';
            scheduleBtn.appendChild(scheduleIcon);
            
            // Delete button
            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'text-red-600 hover:text-red-800 p-2';
            deleteBtn.title = 'Delete image';
            deleteBtn.dataset.fieldId = fieldId;
            deleteBtn.dataset.imageId = String(imgId);
            deleteBtn.dataset.pluginId = pluginId;
            deleteBtn.addEventListener('click', function() {
                window.deleteUploadedImage(this.dataset.fieldId, this.dataset.imageId, this.dataset.pluginId);
            });
            const deleteIcon = document.createElement('i');
            deleteIcon.className = 'fas fa-trash';
            deleteBtn.appendChild(deleteIcon);
            
            rightSection.appendChild(scheduleBtn);
            rightSection.appendChild(deleteBtn);
            
            mainDiv.appendChild(leftSection);
            mainDiv.appendChild(rightSection);
            
            // Create schedule container
            const scheduleContainer = document.createElement('div');
            scheduleContainer.id = `schedule_${sanitizedId}`;
            scheduleContainer.className = 'hidden mt-3 pt-3 border-t border-gray-300';
            
            // Restore preserved schedule content if this is the open one
            if (openScheduleId === sanitizedId && preservedScheduleContent) {
                scheduleContainer.innerHTML = preservedScheduleContent;
                scheduleContainer.classList.remove('hidden');
            }
            
            container.appendChild(mainDiv);
            container.appendChild(scheduleContainer);
            imageList.appendChild(container);
        });
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
        
        // Sanitize imageId to match updateImageList's sanitization
        const sanitizedId = (imageId || imageIdx).toString().replace(/[^a-zA-Z0-9_-]/g, '_');
        const scheduleContainer = document.getElementById(`schedule_${sanitizedId}`);
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
        
        // Use sanitizedId for all ID references in the schedule HTML
        scheduleContainer.innerHTML = `
            <div class="bg-white rounded-lg border border-blue-200 p-4">
                <h4 class="text-sm font-semibold text-gray-900 mb-3">
                    <i class="fas fa-clock mr-2"></i>Schedule Settings
                </h4>
                
                <!-- Enable Schedule -->
                <div class="mb-4">
                    <label class="flex items-center">
                        <input type="checkbox" 
                               id="schedule_enabled_${sanitizedId}"
                               ${schedule.enabled ? 'checked' : ''}
                               onchange="window.toggleImageScheduleEnabled('${fieldId}', '${sanitizedId}', ${imageIdx})"
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <span class="ml-2 text-sm font-medium text-gray-700">Enable schedule for this image</span>
                    </label>
                    <p class="ml-6 text-xs text-gray-500 mt-1">When enabled, this image will only display during scheduled times</p>
                </div>
                
                <!-- Schedule Mode -->
                <div id="schedule_options_${sanitizedId}" class="space-y-4" style="display: ${schedule.enabled ? 'block' : 'none'};">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Schedule Type</label>
                        <select id="schedule_mode_${sanitizedId}"
                                onchange="window.updateImageScheduleMode('${fieldId}', '${sanitizedId}', ${imageIdx})"
                                class="block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                            <option value="always" ${schedule.mode === 'always' ? 'selected' : ''}>Always Show (No Schedule)</option>
                            <option value="time_range" ${schedule.mode === 'time_range' ? 'selected' : ''}>Same Time Every Day</option>
                            <option value="per_day" ${schedule.mode === 'per_day' ? 'selected' : ''}>Different Times Per Day</option>
                        </select>
                    </div>
                    
                    <!-- Time Range Mode -->
                    <div id="time_range_${sanitizedId}" class="grid grid-cols-2 gap-4" style="display: ${schedule.mode === 'time_range' ? 'grid' : 'none'};">
                        <div>
                            <label class="block text-xs font-medium text-gray-700 mb-1">Start Time</label>
                            <input type="time" 
                                   id="schedule_start_${sanitizedId}"
                                   value="${escapeHtml(schedule.start_time || '08:00')}"
                                   onchange="window.updateImageScheduleTime('${fieldId}', '${sanitizedId}', ${imageIdx})"
                                   class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md">
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-700 mb-1">End Time</label>
                            <input type="time" 
                                   id="schedule_end_${sanitizedId}"
                                   value="${escapeHtml(schedule.end_time || '18:00')}"
                                   onchange="window.updateImageScheduleTime('${fieldId}', '${sanitizedId}', ${imageIdx})"
                                   class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md">
                        </div>
                    </div>
                    
                    <!-- Per-Day Mode -->
                    <div id="per_day_${sanitizedId}" style="display: ${schedule.mode === 'per_day' ? 'block' : 'none'};">
                        <label class="block text-xs font-medium text-gray-700 mb-2">Day-Specific Times</label>
                        <div class="bg-gray-50 rounded p-3 space-y-2 max-h-64 overflow-y-auto">
                            ${['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].map(day => {
                                const dayConfig = (schedule.days && schedule.days[day]) || { enabled: true, start_time: '08:00', end_time: '18:00' };
                                return `
                                <div class="bg-white rounded p-2 border border-gray-200">
                                    <div class="flex items-center justify-between mb-2">
                                        <label class="flex items-center">
                                            <input type="checkbox"
                                                   id="day_${day}_${sanitizedId}"
                                                   ${dayConfig.enabled ? 'checked' : ''}
                                                   onchange="window.updateImageScheduleDay('${fieldId}', '${sanitizedId}', ${imageIdx}, '${day}')"
                                                   class="h-3 w-3 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                                            <span class="ml-2 text-xs font-medium text-gray-700 capitalize">${day}</span>
                                        </label>
                                    </div>
                                    <div class="grid grid-cols-2 gap-2 ml-5" id="day_times_${day}_${sanitizedId}" style="display: ${dayConfig.enabled ? 'grid' : 'none'};">
                                        <input type="time"
                                               id="day_${day}_start_${sanitizedId}"
                                               value="${escapeHtml(dayConfig.start_time || '08:00')}"
                                               onchange="window.updateImageScheduleDay('${fieldId}', '${sanitizedId}', ${imageIdx}, '${day}')"
                                               class="text-xs px-2 py-1 border border-gray-300 rounded"
                                               ${!dayConfig.enabled ? 'disabled' : ''}>
                                        <input type="time"
                                               id="day_${day}_end_${sanitizedId}"
                                               value="${escapeHtml(dayConfig.end_time || '18:00')}"
                                               onchange="window.updateImageScheduleDay('${fieldId}', '${sanitizedId}', ${imageIdx}, '${day}')"
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
        
        // Sanitize imageId for DOM lookup
        const sanitizedId = String(imageId).replace(/[^a-zA-Z0-9_-]/g, '_');
        const checkbox = document.getElementById(`schedule_enabled_${sanitizedId}`);
        const enabled = checkbox ? checkbox.checked : false;
        
        if (!image.schedule) {
            image.schedule = { enabled: false, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
        }
        
        image.schedule.enabled = enabled;
        
        const optionsDiv = document.getElementById(`schedule_options_${sanitizedId}`);
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
        
        // Sanitize imageId for DOM lookup
        const sanitizedId = String(imageId).replace(/[^a-zA-Z0-9_-]/g, '_');
        
        if (!image.schedule) {
            image.schedule = { enabled: true, mode: 'always', start_time: '08:00', end_time: '18:00', days: {} };
        }
        
        const modeSelect = document.getElementById(`schedule_mode_${sanitizedId}`);
        const mode = modeSelect ? modeSelect.value : 'always';
        
        image.schedule.mode = mode;
        
        const timeRangeDiv = document.getElementById(`time_range_${sanitizedId}`);
        const perDayDiv = document.getElementById(`per_day_${sanitizedId}`);
        
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
        
        // Sanitize imageId for DOM lookup
        const sanitizedId = String(imageId).replace(/[^a-zA-Z0-9_-]/g, '_');
        
        if (!image.schedule) {
            image.schedule = { enabled: true, mode: 'time_range', start_time: '08:00', end_time: '18:00' };
        }
        
        const startInput = document.getElementById(`schedule_start_${sanitizedId}`);
        const endInput = document.getElementById(`schedule_end_${sanitizedId}`);
        
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
        
        // Sanitize imageId for DOM lookup
        const sanitizedId = String(imageId).replace(/[^a-zA-Z0-9_-]/g, '_');
        
        if (!image.schedule) {
            image.schedule = { enabled: true, mode: 'per_day', days: {} };
        }
        
        if (!image.schedule.days) {
            image.schedule.days = {};
        }
        
        const checkbox = document.getElementById(`day_${day}_${sanitizedId}`);
        const startInput = document.getElementById(`day_${day}_start_${sanitizedId}`);
        const endInput = document.getElementById(`day_${day}_end_${sanitizedId}`);
        
        const enabled = checkbox ? checkbox.checked : true;
        
        if (!image.schedule.days[day]) {
            image.schedule.days[day] = { enabled: true, start_time: '08:00', end_time: '18:00' };
        }
        
        image.schedule.days[day].enabled = enabled;
        if (startInput) image.schedule.days[day].start_time = startInput.value || '08:00';
        if (endInput) image.schedule.days[day].end_time = endInput.value || '18:00';
        
        const dayTimesDiv = document.getElementById(`day_times_${day}_${sanitizedId}`);
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
