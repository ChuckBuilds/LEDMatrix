/**
 * LEDMatrix Font Selector Widget
 *
 * Dynamic font selector that fetches available fonts from the API.
 * Automatically shows all fonts in assets/fonts/ directory.
 *
 * Schema example:
 * {
 *   "font": {
 *     "type": "string",
 *     "title": "Font Family",
 *     "x-widget": "font-selector",
 *     "x-options": {
 *       "placeholder": "Select a font...",
 *       "showPreview": false,
 *       "filterTypes": ["ttf", "bdf"]
 *     },
 *     "default": "PressStart2P-Regular.ttf"
 *   }
 * }
 *
 * @module FontSelectorWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('FontSelector', '1.0.0') : null;

    // Cache for font catalog to avoid repeated API calls
    let fontCatalogCache = null;
    let fontCatalogPromise = null;

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

    /**
     * Generate a human-readable display name from font filename
     * @param {string} filename - Font filename (e.g., "PressStart2P-Regular.ttf")
     * @returns {string} Display name (e.g., "Press Start 2P Regular")
     */
    function generateDisplayName(filename) {
        if (!filename) return '';

        // Remove extension
        let name = filename.replace(/\.(ttf|bdf|otf)$/i, '');

        // Handle common patterns
        // Split on hyphens and underscores
        name = name.replace(/[-_]/g, ' ');

        // Add space before capital letters (camelCase/PascalCase)
        name = name.replace(/([a-z])([A-Z])/g, '$1 $2');

        // Add space before numbers that follow letters
        name = name.replace(/([a-zA-Z])(\d)/g, '$1 $2');

        // Clean up multiple spaces
        name = name.replace(/\s+/g, ' ').trim();

        return name;
    }

    /**
     * Fetch font catalog from API (with caching)
     * @returns {Promise<Array>} Array of font objects
     */
    async function fetchFontCatalog() {
        // Return cached data if available
        if (fontCatalogCache) {
            return fontCatalogCache;
        }

        // Return existing promise if fetch is in progress
        if (fontCatalogPromise) {
            return fontCatalogPromise;
        }

        // Fetch from API
        fontCatalogPromise = fetch('/api/v3/fonts/catalog')
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Failed to fetch font catalog: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                // Handle different response structures
                let fonts = [];

                if (data.data && data.data.fonts) {
                    // New format: { data: { fonts: [...] } }
                    fonts = data.data.fonts;
                } else if (data.data && data.data.catalog) {
                    // Alternative format: { data: { catalog: {...} } }
                    const catalog = data.data.catalog;
                    fonts = Object.entries(catalog).map(([family, info]) => ({
                        filename: info.filename || family,
                        family: family,
                        display_name: info.display_name || generateDisplayName(info.filename || family),
                        path: info.path,
                        type: info.type || 'unknown'
                    }));
                } else if (Array.isArray(data)) {
                    // Direct array format
                    fonts = data;
                }

                // Sort fonts alphabetically by display name
                fonts.sort((a, b) => {
                    const nameA = (a.display_name || a.filename || '').toLowerCase();
                    const nameB = (b.display_name || b.filename || '').toLowerCase();
                    return nameA.localeCompare(nameB);
                });

                fontCatalogCache = fonts;
                fontCatalogPromise = null;
                return fonts;
            })
            .catch(error => {
                console.error('[FontSelectorWidget] Error fetching font catalog:', error);
                fontCatalogPromise = null;
                return [];
            });

        return fontCatalogPromise;
    }

    /**
     * Clear the font catalog cache (call when fonts are uploaded/deleted)
     */
    function clearFontCache() {
        fontCatalogCache = null;
        fontCatalogPromise = null;
    }

    // Expose cache clearing function globally
    window.clearFontSelectorCache = clearFontCache;

    // Guard against missing global registry
    if (!window.LEDMatrixWidgets || typeof window.LEDMatrixWidgets.register !== 'function') {
        console.error('[FontSelectorWidget] LEDMatrixWidgets registry not available');
        return;
    }

    window.LEDMatrixWidgets.register('font-selector', {
        name: 'Font Selector Widget',
        version: '1.0.0',

        render: async function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'font-select');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const placeholder = xOptions.placeholder || 'Select a font...';
            const filterTypes = xOptions.filterTypes || null; // e.g., ['ttf', 'bdf']
            const showPreview = xOptions.showPreview === true;
            const disabled = xOptions.disabled === true;
            const required = xOptions.required === true;

            const currentValue = value !== null && value !== undefined ? String(value) : '';

            // Show loading state
            container.innerHTML = `
                <div id="${fieldId}_widget" class="font-selector-widget" data-field-id="${fieldId}">
                    <select id="${fieldId}_input"
                            name="${escapeHtml(options.name || fieldId)}"
                            disabled
                            class="form-select w-full rounded-md border-gray-300 shadow-sm bg-gray-100 text-black">
                        <option value="">Loading fonts...</option>
                    </select>
                </div>
            `;

            try {
                // Fetch fonts from API
                const fonts = await fetchFontCatalog();

                // Filter by type if specified
                let filteredFonts = fonts;
                if (filterTypes && Array.isArray(filterTypes)) {
                    filteredFonts = fonts.filter(font => {
                        const fontType = (font.type || '').toLowerCase();
                        return filterTypes.some(t => t.toLowerCase() === fontType);
                    });
                }

                // Build select HTML
                let html = `<div id="${fieldId}_widget" class="font-selector-widget" data-field-id="${fieldId}">`;

                html += `
                    <select id="${fieldId}_input"
                            name="${escapeHtml(options.name || fieldId)}"
                            ${disabled ? 'disabled' : ''}
                            ${required ? 'required' : ''}
                            onchange="window.LEDMatrixWidgets.getHandlers('font-selector').onChange('${fieldId}')"
                            class="form-select w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black">
                `;

                // Placeholder option
                if (placeholder && !required) {
                    html += `<option value="" ${!currentValue ? 'selected' : ''}>${escapeHtml(placeholder)}</option>`;
                }

                // Font options
                for (const font of filteredFonts) {
                    const fontValue = font.filename || font.family;
                    const displayName = font.display_name || generateDisplayName(fontValue);
                    const fontType = font.type ? ` (${font.type.toUpperCase()})` : '';
                    const isSelected = String(fontValue) === currentValue;

                    html += `<option value="${escapeHtml(String(fontValue))}" ${isSelected ? 'selected' : ''}>${escapeHtml(displayName)}${escapeHtml(fontType)}</option>`;
                }

                html += '</select>';

                // Optional preview area
                if (showPreview) {
                    html += `
                        <div id="${fieldId}_preview" class="mt-2 p-2 bg-gray-800 rounded text-white text-center" style="min-height: 30px;">
                            <span style="font-family: monospace;">Preview</span>
                        </div>
                    `;
                }

                // Error message area
                html += `<div id="${fieldId}_error" class="text-sm text-red-600 mt-1 hidden"></div>`;

                html += '</div>';

                container.innerHTML = html;

            } catch (error) {
                console.error('[FontSelectorWidget] Error rendering:', error);
                container.innerHTML = `
                    <div id="${fieldId}_widget" class="font-selector-widget" data-field-id="${fieldId}">
                        <select id="${fieldId}_input"
                                name="${escapeHtml(options.name || fieldId)}"
                                class="form-select w-full rounded-md border-gray-300 shadow-sm bg-white text-black">
                            <option value="${escapeHtml(currentValue)}" selected>${escapeHtml(currentValue || 'Error loading fonts')}</option>
                        </select>
                        <div class="text-sm text-red-600 mt-1">Failed to load font list</div>
                    </div>
                `;
            }
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
                input.value = value !== null && value !== undefined ? String(value) : '';
            }
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('font-selector');
                triggerChange(fieldId, widget.getValue(fieldId));
            }
        },

        // Expose utility functions
        utils: {
            clearCache: clearFontCache,
            fetchCatalog: fetchFontCatalog,
            generateDisplayName: generateDisplayName
        }
    });

    console.log('[FontSelectorWidget] Font selector widget registered');
})();
