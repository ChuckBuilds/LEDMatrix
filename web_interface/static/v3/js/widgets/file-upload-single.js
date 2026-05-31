/**
 * LEDMatrix File Upload Single Widget
 *
 * Single-image upload for string fields. Uploads to the plugin's asset folder
 * and sets the string field value to the returned relative path.
 * Designed for per-item image fields within array-table rows.
 *
 * The plugin_id is injected automatically from the template context
 * via options.pluginId — no need to specify it in the schema.
 *
 * Schema example (any plugin):
 * {
 *   "image_path": {
 *     "type": "string",
 *     "x-widget": "file-upload-single",
 *     "x-upload-config": {
 *       "allowed_types": ["image/png", "image/jpeg", "image/bmp", "image/gif"],
 *       "max_size_mb": 5
 *     }
 *   }
 * }
 *
 * @module FileUploadSingleWidget
 */

(function() {
    'use strict';

    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[FileUploadSingleWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    const base = window.BaseWidget ? new window.BaseWidget('FileUploadSingle', '1.0.0') : null;

    function escapeHtml(text) {
        if (base) return base.escapeHtml(text);
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function sanitizeId(id) {
        if (base) return base.sanitizeId(id);
        return String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    function triggerChange(fieldId, value) {
        if (base) {
            base.triggerChange(fieldId, value);
        } else {
            document.dispatchEvent(new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true,
                cancelable: true
            }));
        }
    }

    function isImagePath(path) {
        if (!path) return false;
        return /\.(png|jpg|jpeg|bmp|gif)$/i.test(path);
    }

    function safeSetHTML(target, html) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        target.textContent = '';
        const frag = document.createDocumentFragment();
        Array.from(doc.body.childNodes).forEach(function(n) { frag.appendChild(n); });
        target.appendChild(frag);
    }

    window.LEDMatrixWidgets.register('file-upload-single', {
        name: 'File Upload Single Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'file_upload_single');
            const uploadConfig = config['x-upload-config'] || config['x_upload_config'] || {};
            const allowedTypes = (uploadConfig.allowed_types || ['image/png', 'image/jpeg', 'image/bmp', 'image/gif']).join(',');
            const maxSizeMb = uploadConfig.max_size_mb || 5;
            const pluginId = options.pluginId || '';
            const currentValue = value || '';
            const hasImage = isImagePath(currentValue);

            let html = `<div id="${fieldId}_widget" class="file-upload-single-widget" data-field-id="${fieldId}" data-plugin-id="${escapeHtml(pluginId)}">`;

            // Hidden input carries the actual string value
            html += `<input type="hidden" id="${fieldId}" name="${escapeHtml(options.name || fieldId)}" value="${escapeHtml(currentValue)}">`;

            // Preview area (shown when a value is set)
            html += `<div id="${fieldId}_preview" class="${hasImage ? '' : 'hidden'} flex items-center space-x-3 mb-2 p-2 bg-gray-50 rounded border border-gray-200">`;
            html += `<img id="${fieldId}_thumb" src="/${escapeHtml(currentValue)}" alt="Preview"
                          class="w-12 h-12 object-cover rounded"
                          onerror="this.style.display='none';document.getElementById('${fieldId}_thumb_placeholder').style.display='flex'">`;
            html += `<div id="${fieldId}_thumb_placeholder" style="display:none" class="w-12 h-12 bg-gray-200 rounded flex items-center justify-center">
                         <i class="fas fa-image text-gray-400 text-lg"></i>
                     </div>`;
            html += `<div class="flex-1 min-w-0">
                         <p id="${fieldId}_filename" class="text-xs text-gray-600 truncate">${escapeHtml(currentValue.split('/').pop() || '')}</p>
                         <p id="${fieldId}_fullpath" class="text-xs text-gray-400">${escapeHtml(currentValue)}</p>
                     </div>`;
            html += `<button type="button"
                             onclick="window.LEDMatrixWidgets.getHandlers('file-upload-single').onClear('${fieldId}')"
                             class="flex-shrink-0 text-red-400 hover:text-red-600 p-1" title="Remove image">
                         <i class="fas fa-times"></i>
                     </button>`;
            html += '</div>';

            // Upload drop zone — keyboard accessible via tabindex + Enter/Space
            html += `<div id="${fieldId}_drop_zone"
                          class="border-2 border-dashed border-gray-300 rounded-lg p-3 text-center hover:border-blue-400 transition-colors cursor-pointer"
                          role="button" tabindex="0"
                          aria-label="${hasImage ? 'Replace image' : 'Upload image'}"
                          ondrop="window.LEDMatrixWidgets.getHandlers('file-upload-single').onDrop(event, '${fieldId}')"
                          ondragover="event.preventDefault()"
                          onclick="document.getElementById('${fieldId}_file_input').click()"
                          onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();document.getElementById('${fieldId}_file_input').click();}">
                         <input type="file"
                                id="${fieldId}_file_input"
                                accept="${escapeHtml(allowedTypes)}"
                                style="display:none"
                                data-field-id="${fieldId}"
                                data-plugin-id="${escapeHtml(pluginId)}"
                                data-max-size-mb="${maxSizeMb}"
                                data-allowed-types="${escapeHtml(allowedTypes)}"
                                onchange="window.LEDMatrixWidgets.getHandlers('file-upload-single').onFileSelect(event, '${fieldId}')">
                         <i class="fas fa-cloud-upload-alt text-xl text-gray-400 mb-1"></i>
                         <p class="text-xs text-gray-500">${hasImage ? 'Click to replace image' : 'Click or drag to upload image'}</p>
                         <p class="text-xs text-gray-400">Max ${maxSizeMb}MB</p>
                     </div>`;

            // Status area for upload feedback
            html += `<div id="${fieldId}_status" class="mt-1 text-xs hidden"></div>`;

            html += '</div>';
            safeSetHTML(container, html);
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(safeId);
            return input ? input.value : '';
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const hidden = document.getElementById(safeId);
            const preview = document.getElementById(`${safeId}_preview`);
            const thumb = document.getElementById(`${safeId}_thumb`);
            const thumbPlaceholder = document.getElementById(`${safeId}_thumb_placeholder`);
            const filename = document.getElementById(`${safeId}_filename`);
            const dropZone = document.getElementById(`${safeId}_drop_zone`);

            if (hidden) hidden.value = value || '';

            const hasImage = isImagePath(value);
            if (preview) preview.classList.toggle('hidden', !hasImage);
            if (thumb && hasImage) {
                thumb.src = `/${value}`;
                thumb.style.display = '';
                if (thumbPlaceholder) thumbPlaceholder.style.display = 'none';
            }
            if (filename) filename.textContent = hasImage ? value.split('/').pop() : '';
            const fullpath = document.getElementById(`${safeId}_fullpath`);
            if (fullpath) fullpath.textContent = value || '';

            // Update drop zone hint text
            const hint = dropZone ? dropZone.querySelector('p') : null;
            if (hint) hint.textContent = hasImage ? 'Click to replace image' : 'Click or drag to upload image';
        },

        handlers: {
            onFileSelect: function(event, fieldId) {
                const files = event.target.files;
                if (files && files.length > 0) {
                    window.LEDMatrixWidgets.getHandlers('file-upload-single').uploadFile(fieldId, files[0]);
                }
            },

            onDrop: function(event, fieldId) {
                event.preventDefault();
                const files = event.dataTransfer.files;
                if (files && files.length > 0) {
                    window.LEDMatrixWidgets.getHandlers('file-upload-single').uploadFile(fieldId, files[0]);
                }
            },

            onClear: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('file-upload-single');
                widget.setValue(fieldId, '');
                triggerChange(fieldId, '');
                // Reset file input so the same file can be re-selected
                const fileInput = document.getElementById(`${sanitizeId(fieldId)}_file_input`);
                if (fileInput) fileInput.value = '';
            },

            uploadFile: async function(fieldId, file) {
                const safeId = sanitizeId(fieldId);
                const fileInput = document.getElementById(`${safeId}_file_input`);
                const statusDiv = document.getElementById(`${safeId}_status`);
                const notifyFn = window.showNotification || console.log;

                // Read config from the file input data attributes
                const pluginId = (fileInput && fileInput.dataset.pluginId) || '';
                const maxSizeMb = parseFloat((fileInput && fileInput.dataset.maxSizeMb) || '5');
                const allowedTypes = ((fileInput && fileInput.dataset.allowedTypes) || 'image/png,image/jpeg,image/bmp,image/gif')
                    .split(',').map(t => t.trim());

                if (!pluginId) {
                    notifyFn('Plugin ID not set — cannot upload', 'error');
                    return;
                }

                // Validate type
                if (!allowedTypes.includes(file.type)) {
                    notifyFn(`File type "${file.type}" not allowed`, 'error');
                    return;
                }

                // Validate size
                if (file.size > maxSizeMb * 1024 * 1024) {
                    notifyFn(`File exceeds ${maxSizeMb}MB limit`, 'error');
                    return;
                }

                // Show uploading status — use DOM methods to avoid innerHTML with dynamic data
                if (statusDiv) {
                    statusDiv.className = 'mt-1 text-xs text-gray-500';
                    statusDiv.textContent = '';
                    const spinner = document.createElement('i');
                    spinner.className = 'fas fa-spinner fa-spin mr-1';
                    statusDiv.appendChild(spinner);
                    statusDiv.appendChild(document.createTextNode('Uploading…'));
                }

                const formData = new FormData();
                formData.append('plugin_id', pluginId);
                formData.append('files', file);

                try {
                    const response = await fetch('/api/v3/plugins/assets/upload', {
                        method: 'POST',
                        body: formData
                    });

                    if (!response.ok) {
                        const body = await response.text();
                        throw new Error(`Server error ${response.status}: ${body}`);
                    }

                    const data = await response.json();

                    if (data.status === 'success' && data.uploaded_files && data.uploaded_files.length > 0) {
                        const uploadedPath = data.uploaded_files[0].path;
                        const widget = window.LEDMatrixWidgets.get('file-upload-single');
                        widget.setValue(fieldId, uploadedPath);
                        triggerChange(fieldId, uploadedPath);

                        if (statusDiv) {
                            statusDiv.className = 'mt-1 text-xs text-green-600';
                            statusDiv.textContent = '';
                            const icon = document.createElement('i');
                            icon.className = 'fas fa-check-circle mr-1';
                            statusDiv.appendChild(icon);
                            statusDiv.appendChild(document.createTextNode('Uploaded successfully'));
                            setTimeout(() => { statusDiv.className = 'mt-1 text-xs hidden'; statusDiv.textContent = ''; }, 3000);
                        }
                        notifyFn('Image uploaded successfully', 'success');
                    } else {
                        throw new Error(data.message || 'Upload failed');
                    }
                } catch (error) {
                    if (statusDiv) {
                        statusDiv.className = 'mt-1 text-xs text-red-600';
                        statusDiv.textContent = '';
                        const errIcon = document.createElement('i');
                        errIcon.className = 'fas fa-exclamation-circle mr-1';
                        statusDiv.appendChild(errIcon);
                        statusDiv.appendChild(document.createTextNode(error.message || 'Upload failed'));
                    }
                    notifyFn(`Upload error: ${error.message}`, 'error');
                } finally {
                    if (fileInput) fileInput.value = '';
                }
            }
        }
    });

    console.log('[FileUploadSingleWidget] File upload single widget registered');
})();
