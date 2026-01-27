/**
 * LEDMatrix URL Input Widget
 *
 * URL input with validation and protocol handling.
 *
 * Schema example:
 * {
 *   "website": {
 *     "type": "string",
 *     "format": "uri",
 *     "x-widget": "url-input",
 *     "x-options": {
 *       "placeholder": "https://example.com",
 *       "showIcon": true,
 *       "allowedProtocols": ["http", "https"],
 *       "showPreview": true
 *     }
 *   }
 * }
 *
 * @module UrlInputWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('UrlInput', '1.0.0') : null;

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
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true,
                cancelable: true
            });
            document.dispatchEvent(event);
        }
    }

    function isValidUrl(string, allowedProtocols) {
        try {
            const url = new URL(string);
            if (allowedProtocols && allowedProtocols.length > 0) {
                const protocol = url.protocol.replace(':', '');
                return allowedProtocols.includes(protocol);
            }
            return true;
        } catch (_) {
            return false;
        }
    }

    window.LEDMatrixWidgets.register('url-input', {
        name: 'URL Input Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'url_input');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const placeholder = xOptions.placeholder || 'https://example.com';
            const showIcon = xOptions.showIcon !== false;
            const showPreview = xOptions.showPreview === true;
            // Normalize allowedProtocols to an array
            let allowedProtocols = xOptions.allowedProtocols;
            if (typeof allowedProtocols === 'string') {
                allowedProtocols = allowedProtocols.split(',').map(p => p.trim()).filter(p => p);
            } else if (!Array.isArray(allowedProtocols)) {
                allowedProtocols = ['http', 'https'];
            }
            // Filter to only valid protocol strings (alphanumeric only)
            allowedProtocols = allowedProtocols.map(p => String(p).replace(/[^a-zA-Z0-9]/g, '')).filter(p => p);
            if (allowedProtocols.length === 0) {
                allowedProtocols = ['http', 'https'];
            }

            const disabled = xOptions.disabled === true;
            const required = xOptions.required === true;

            const currentValue = value || '';

            // Escape the protocols for safe HTML attribute interpolation
            const escapedProtocols = escapeHtml(allowedProtocols.join(','));
            let html = `<div id="${fieldId}_widget" class="url-input-widget" data-field-id="${fieldId}" data-protocols="${escapedProtocols}">`;

            html += '<div class="relative">';

            if (showIcon) {
                html += `
                    <div class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
                        <i class="fas fa-link text-gray-400"></i>
                    </div>
                `;
            }

            html += `
                <input type="url"
                       id="${fieldId}_input"
                       name="${escapeHtml(options.name || fieldId)}"
                       value="${escapeHtml(currentValue)}"
                       placeholder="${escapeHtml(placeholder)}"
                       ${disabled ? 'disabled' : ''}
                       ${required ? 'required' : ''}
                       onchange="window.LEDMatrixWidgets.getHandlers('url-input').onChange('${fieldId}')"
                       oninput="window.LEDMatrixWidgets.getHandlers('url-input').onInput('${fieldId}')"
                       class="form-input w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${showIcon ? 'pl-10' : ''} ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black placeholder:text-gray-400">
            `;

            html += '</div>';

            // Preview link (if enabled and value exists)
            if (showPreview) {
                html += `
                    <div id="${fieldId}_preview" class="mt-2 ${currentValue && isValidUrl(currentValue, allowedProtocols) ? '' : 'hidden'}">
                        <a id="${fieldId}_preview_link"
                           href="${escapeHtml(currentValue)}"
                           target="_blank"
                           rel="noopener noreferrer"
                           class="text-sm text-blue-600 hover:text-blue-800 flex items-center">
                            <i class="fas fa-external-link-alt mr-1 text-xs"></i>
                            <span>Open link in new tab</span>
                        </a>
                    </div>
                `;
            }

            // Error message area
            html += `<div id="${fieldId}_error" class="text-sm text-red-600 mt-1 hidden"></div>`;

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            return input ? input.value : '';
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            if (input) {
                input.value = value || '';
                this.handlers.onInput(fieldId);
            }
        },

        validate: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const errorEl = document.getElementById(`${safeId}_error`);
            const widget = document.getElementById(`${safeId}_widget`);

            if (!input) return { valid: true, errors: [] };

            const value = input.value;
            const protocols = widget?.dataset.protocols?.split(',') || ['http', 'https'];

            let isValid = true;
            let errorMsg = '';

            // First check browser validation (required, type, etc.)
            if (!input.checkValidity()) {
                isValid = false;
                errorMsg = input.validationMessage;
            } else if (value) {
                // Then check custom protocol validation
                if (!isValidUrl(value, protocols)) {
                    isValid = false;
                    errorMsg = `Please enter a valid URL (${protocols.join(', ')} only)`;
                }
            }

            if (errorEl) {
                if (!isValid) {
                    errorEl.textContent = errorMsg;
                    errorEl.classList.remove('hidden');
                    input.classList.add('border-red-500');
                } else {
                    errorEl.classList.add('hidden');
                    input.classList.remove('border-red-500');
                }
            }

            return { valid: isValid, errors: isValid ? [] : [errorMsg] };
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('url-input');
                widget.validate(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            onInput: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const input = document.getElementById(`${safeId}_input`);
                const previewEl = document.getElementById(`${safeId}_preview`);
                const previewLink = document.getElementById(`${safeId}_preview_link`);
                const widgetEl = document.getElementById(`${safeId}_widget`);

                const value = input?.value || '';
                const protocols = widgetEl?.dataset.protocols?.split(',') || ['http', 'https'];

                if (previewEl && previewLink) {
                    if (value && isValidUrl(value, protocols)) {
                        previewLink.href = value;
                        previewEl.classList.remove('hidden');
                    } else {
                        previewEl.classList.add('hidden');
                    }
                }

                // Validate on input
                const widget = window.LEDMatrixWidgets.get('url-input');
                widget.validate(fieldId);
            }
        }
    });

    console.log('[UrlInputWidget] URL input widget registered');
})();
