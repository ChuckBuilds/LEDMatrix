/**
 * Plugin Order List — shared drag-and-drop reorder list of enabled plugins.
 *
 * Factored out of the Vegas Scroll section of display.html so both Vegas mode
 * and the primary rotation (Durations tab) use one implementation. Renders
 * one draggable row per enabled plugin into a container and keeps a hidden
 * input's value in sync as a JSON array of plugin ids in display order.
 *
 * Usage:
 *   PluginOrderList.init({
 *       containerId: 'vegas_plugin_order',        // rows render here
 *       orderInputId: 'vegas_plugin_order_value', // hidden input, JSON array of ids
 *       excludedInputId: 'vegas_excluded_plugins_value', // optional: adds an
 *           // include-checkbox per row; unchecked ids collect here (JSON array)
 *       showVegasModeBadge: true                  // optional: Scroll/Fixed/Static badge
 *   });
 *
 * The container re-renders from /api/v3/plugins/installed each init; the
 * hidden input(s) must already hold the saved order/exclusions (JSON).
 */
(function() {
    'use strict';

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function escapeAttr(text) {
        return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    const MODE_LABELS = {
        'scroll': { label: 'Scroll', icon: 'fa-scroll', color: 'text-blue-600' },
        'fixed':  { label: 'Fixed',  icon: 'fa-square', color: 'text-green-600' },
        'static': { label: 'Static', icon: 'fa-pause',  color: 'text-orange-600' }
    };

    function init(options) {
        const container = document.getElementById(options.containerId);
        const orderInput = document.getElementById(options.orderInputId);
        const excludedInput = options.excludedInputId ? document.getElementById(options.excludedInputId) : null;
        if (!container || !orderInput) return;

        function syncInputs() {
            const order = [];
            const excluded = [];
            container.querySelectorAll('.plugin-order-item').forEach(item => {
                const pluginId = item.dataset.pluginId;
                order.push(pluginId);
                const checkbox = item.querySelector('.plugin-order-include');
                if (checkbox && !checkbox.checked) excluded.push(pluginId);
            });
            orderInput.value = JSON.stringify(order);
            if (excludedInput) excludedInput.value = JSON.stringify(excluded);
        }

        function setupDragAndDrop() {
            let draggedItem = null;
            container.querySelectorAll('.plugin-order-item').forEach(item => {
                item.addEventListener('dragstart', function(e) {
                    draggedItem = this;
                    this.style.opacity = '0.5';
                    e.dataTransfer.effectAllowed = 'move';
                });
                item.addEventListener('dragend', function() {
                    this.style.opacity = '1';
                    draggedItem = null;
                    syncInputs();
                });
                item.addEventListener('dragover', function(e) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    const rect = this.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    if (e.clientY < midY) {
                        this.style.borderTop = '2px solid #3b82f6';
                        this.style.borderBottom = '';
                    } else {
                        this.style.borderBottom = '2px solid #3b82f6';
                        this.style.borderTop = '';
                    }
                });
                item.addEventListener('dragleave', function() {
                    this.style.borderTop = '';
                    this.style.borderBottom = '';
                });
                item.addEventListener('drop', function(e) {
                    e.preventDefault();
                    this.style.borderTop = '';
                    this.style.borderBottom = '';
                    if (draggedItem && draggedItem !== this) {
                        const rect = this.getBoundingClientRect();
                        const midY = rect.top + rect.height / 2;
                        if (e.clientY < midY) {
                            container.insertBefore(draggedItem, this);
                        } else {
                            container.insertBefore(draggedItem, this.nextSibling);
                        }
                    }
                });
            });
        }

        fetch('/api/v3/plugins/installed')
            .then(response => response.json())
            .then(data => {
                const allPlugins = (data.data && data.data.plugins) || data.plugins || [];
                const plugins = allPlugins.filter(p => p.enabled);
                if (plugins.length === 0) {
                    container.innerHTML = '<p class="text-sm text-gray-500 italic">No enabled plugins</p>';
                    return;
                }

                let currentOrder = [];
                let excluded = [];
                try {
                    currentOrder = JSON.parse(orderInput.value || '[]');
                    if (excludedInput) excluded = JSON.parse(excludedInput.value || '[]');
                } catch (e) {
                    console.error('Error parsing saved plugin order:', e);
                }

                // Saved order first, then any newly enabled plugins.
                const orderedPlugins = [];
                currentOrder.forEach(id => {
                    const plugin = plugins.find(p => p.id === id);
                    if (plugin) orderedPlugins.push(plugin);
                });
                plugins.forEach(plugin => {
                    if (!orderedPlugins.find(p => p.id === plugin.id)) orderedPlugins.push(plugin);
                });

                let html = '';
                orderedPlugins.forEach(plugin => {
                    const safePluginId = escapeAttr(plugin.id);
                    const safePluginName = escapeHtml(plugin.name || plugin.id);
                    let rowInner;
                    if (excludedInput) {
                        const isExcluded = excluded.includes(plugin.id);
                        rowInner = `
                            <label class="flex items-center flex-1">
                                <input type="checkbox"
                                       class="plugin-order-include h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded mr-2"
                                       ${!isExcluded ? 'checked' : ''}>
                                <span class="text-sm font-medium text-gray-700">${safePluginName}</span>
                            </label>`;
                    } else {
                        rowInner = `<span class="text-sm font-medium text-gray-700 flex-1">${safePluginName}</span>`;
                    }
                    let badge = '';
                    if (options.showVegasModeBadge) {
                        const vegasMode = plugin.vegas_mode || plugin.vegas_content_type || 'fixed';
                        const modeInfo = MODE_LABELS[vegasMode] || MODE_LABELS['fixed'];
                        badge = `
                            <span class="text-xs ${modeInfo.color} ml-2" title="Vegas display mode: ${modeInfo.label}">
                                <i class="fas ${modeInfo.icon} mr-1"></i>${modeInfo.label}
                            </span>`;
                    }
                    html += `
                        <div class="flex items-center p-2 bg-gray-50 rounded border border-gray-200 cursor-move plugin-order-item"
                             data-plugin-id="${safePluginId}" draggable="true">
                            <i class="fas fa-grip-vertical text-gray-400 mr-3"></i>
                            ${rowInner}${badge}
                        </div>`;
                });
                container.innerHTML = html;

                setupDragAndDrop();
                container.querySelectorAll('.plugin-order-include').forEach(checkbox => {
                    checkbox.addEventListener('change', syncInputs);
                });
                syncInputs();
            })
            .catch(error => {
                console.error('Error fetching plugins:', error);
                container.innerHTML = '<p class="text-sm text-red-500">Error loading plugins</p>';
            });
    }

    window.PluginOrderList = { init: init };
})();
